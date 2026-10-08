from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field

from agent.modules.agents import AgentConfig, get_catalog_service
from agent.modules.decisions import (
    ChoiceAnswer,
    ChoiceQuestion,
    CloudflareClefClient,
    DecisionClient,
    DecisionError,
    MockDecisionClient,
)

from agent.modules.workflows.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.registry import (
    GraphRegistry,
)
from agent.modules.workflows.run_config import (
    WorkflowContext,
    make_context,
)
from agent.modules.workflows.state.base import BaseState
from agent.modules.workflows.constants import (
    ROUTER_GRAPH_TYPE,
    REACT_AGENT_GRAPH_TYPE,
    DEFAULT_AGENT_NAME,
    STRIP_PREFIXES,
    STRIP_QUOTES,
)
from agent.modules.providers import get_resolved_chat_model
from agent.modules.workflows.model_effort import get_workflow_reasoning_effort_kwargs
from agent.modules.usage import with_usage_tracking
from agent.modules.prompt_variables import get_runtime_prompt_variable_values
from agent.modules.workflows.prompt_builders import (
    replace_known_prompt_placeholders,
    resolve_prompt_variables,
)
from agent.shared.infrastructure.parsing import safe_str_strip

logger = logging.getLogger(__name__)

RoutingMode = Literal["llm_only", "clef_only", "shadow", "cascade"]

_ALLOWED_ROUTING_MODES = frozenset({"llm_only", "clef_only", "shadow", "cascade"})

_default_decision_client: DecisionClient | None = None


def set_default_decision_client(client: DecisionClient | None) -> None:
    """Set global fallback decision client (primarily for testing)."""
    global _default_decision_client
    _default_decision_client = client


class RouterTelemetry(BaseModel):
    """Execution telemetry captured for router evaluation."""

    model_config = ConfigDict(extra="allow")

    mode: str
    selected_agent: str
    source: Literal["llm", "clef", "fallback"]
    clef_latency_ms: float | None = None
    llm_latency_ms: float | None = None
    latency_delta_ms: float | None = None
    clef_confidence: float | None = None
    confidence_threshold: float | None = None
    agreement: bool | None = None
    cascade_triggered: bool = False
    cascade_reason: str | None = None
    error: str | None = None
    fallback_used: bool = False
    fallback_target: str | None = None


class RouterState(BaseState):
    """State for router graph with routing decision fields."""

    target_agent: AgentConfig | None = None
    routing_telemetry: dict[str, Any] | None = None


class _RouteDecision(BaseModel):
    selected_agent: str = Field(
        default="",
        description="Exact target agent name from the candidate list.",
    )

def _agent_description(config: AgentConfig) -> str:
    description = safe_str_strip(getattr(config, "description", ""))
    if description:
        return description
    return "No description provided"


def _build_router_system(
    *,
    user_input: str,
    candidates: dict[str, AgentConfig],
    router_prompt_template: str,
    caller_agent_name: str,
    prompt_variables: dict[str, str] | None = None,
) -> str:
    if not candidates:
        raise RuntimeError("No candidate agents are available for routing.")

    template = safe_str_strip(router_prompt_template)
    if not template:
        raise RuntimeError(
            f"Router agent '{caller_agent_name}' must define a non-empty system_prompt in its card."
        )

    agent_options = "\n".join(
        f"- {name}: {_agent_description(config)}"
        for name, config in candidates.items()
    )

    return replace_known_prompt_placeholders(
        resolve_prompt_variables(template, prompt_variables),
        {
            "agent_options": agent_options,
            "user_input": user_input,
            "caller_agent_name": caller_agent_name,
        },
    )


def _normalize_agent_name(raw_value: object) -> str:
    content = str(raw_value).strip()
    if not content:
        return ""

    first_line = content.split('\n', 1)[0]
    first_line = first_line.lstrip(STRIP_PREFIXES)
    return first_line.strip(STRIP_QUOTES).lower()


def _default_workflow(workflows: dict[str, str]) -> str:
    if REACT_AGENT_GRAPH_TYPE in workflows:
        return REACT_AGENT_GRAPH_TYPE
    return next(iter(workflows))


def _build_candidate_agents(
    caller_agent_name: str,
    catalog: Any,
) -> dict[str, AgentConfig]:
    candidates: dict[str, AgentConfig] = {}
    callable_agent_names = catalog.get_callable_agents(caller_agent_name) or []

    for agent_name in callable_agent_names:
        normalized_name = str(agent_name).strip()
        if not normalized_name or normalized_name == caller_agent_name:
            continue
        config = catalog.get_agent(normalized_name)
        if config is not None:
            candidates[normalized_name] = config
    return candidates


async def _route_agent_name(
    user_input: str,
    candidates: dict[str, AgentConfig],
    router_prompt_template: str,
    caller_agent_name: str,
    provider: str | None = None,
    model: str | None = None,
    prompt_variables: dict[str, str] | None = None,
    config: RunnableConfig | None = None,
    context: WorkflowContext | None = None,
    agent_config: AgentConfig | None = None,
) -> str:
    resolved = get_resolved_chat_model(provider_name=provider, model=model)
    llm = resolved.model
    model_kwargs = (
        get_workflow_reasoning_effort_kwargs(context, agent_config, resolved)
        if agent_config is not None else {}
    )
    router_system = _build_router_system(
        user_input=user_input,
        candidates=candidates,
        router_prompt_template=router_prompt_template,
        caller_agent_name=caller_agent_name,
        prompt_variables=prompt_variables,
    )
    messages = [
        SystemMessage(content=router_system),
        HumanMessage(content=user_input),
    ]

    try:
        usage_config = with_usage_tracking(
            config,
            agent_name=caller_agent_name,
            provider_name=resolved.provider_name,
            model_name=resolved.model_name,
            call_kind="router",
            internal=True,
        )
        decision = await llm.with_structured_output(_RouteDecision).ainvoke(
            messages,
            config=usage_config,
            **model_kwargs,
        )
        return _normalize_agent_name(decision.selected_agent)
    except Exception as exc:
        logger.warning("Structured output failed, falling back to text parsing: %s", exc)
        response = await llm.ainvoke(messages, config=usage_config, **model_kwargs)
        content = getattr(response, "content", "")
        return _normalize_agent_name(content if content else "")


def _resolve_routing_mode(config: RunnableConfig | None = None) -> str:
    """Resolve routing mode from runnable config or runtime settings service."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    mode = None
    for key in ("router_mode", "decision.router.mode", "decision_router_mode"):
        value = configurable.get(key)
        if value is not None and str(value).strip() != "":
            mode = value
            break
    if mode is not None:
        normalized = str(mode).strip().lower()
        if normalized in _ALLOWED_ROUTING_MODES:
            return normalized
        logger.warning("Unknown routing mode '%s', falling back to 'cascade'.", mode)
        return "cascade"

    try:
        from agent.shared.config import get_config_service
        config_service = get_config_service()
        mode = (
            config_service.get_str("decision.router.mode", "")
            or config_service.get_str("router.mode", "")
            or "cascade"
        )
        normalized = str(mode).strip().lower() or "cascade"
        if normalized in _ALLOWED_ROUTING_MODES:
            return normalized
        logger.warning("Unknown routing mode '%s' in config service, falling back to 'cascade'.", mode)
        return "cascade"
    except Exception:
        return "cascade"


def _resolve_confidence_threshold(config: RunnableConfig | None = None) -> float:
    """Resolve confidence threshold for cascade mode (default 0.75)."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    threshold = None
    for key in ("confidence_threshold", "decision.router.threshold", "decision_router_threshold"):
        value = configurable.get(key)
        if value is not None:
            threshold = value
            break
    if threshold is not None:
        try:
            return float(threshold)
        except (ValueError, TypeError):
            pass

    try:
        from agent.shared.config import get_config_service
        config_service = get_config_service()
        for key in ("decision.router.threshold", "router.confidence_threshold"):
            val = config_service.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return 0.75


def _resolve_decision_client(
    decision_client: DecisionClient | None = None,
    config: RunnableConfig | None = None,
) -> DecisionClient | None:
    """Resolve a DecisionClient from explicit arg, config, container, or environment."""
    if decision_client is not None:
        return decision_client

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    client_from_config = configurable.get("decision_client")
    if client_from_config is not None:
        return client_from_config

    if _default_decision_client is not None:
        return _default_decision_client

    # Check AppContainer
    try:
        from agent.bootstrap.container import get_active_container
        container = get_active_container()
        if container is not None and hasattr(container, "decision_service"):
            service = getattr(container, "decision_service")
            if service is not None:
                if hasattr(service, "client") and getattr(service, "client") is not None:
                    return getattr(service, "client")
                if isinstance(service, DecisionClient):
                    return service
    except Exception:
        pass

    # Check runtime config / env for Cloudflare credentials or mock mode
    try:
        from agent.shared.config import get_config_service
        config_service = get_config_service()
        provider = config_service.get_str("decision.provider", "").strip().lower()
        if provider == "mock":
            return MockDecisionClient()

        from agent.modules.decisions import create_decision_client, load_decision_settings
        return create_decision_client(load_decision_settings(config_service))
    except Exception as exc:
        logger.debug("Failed resolving decision client from settings: %s", exc)

    return None


def _extract_clef_choice(answer: Any) -> str:
    """Safely extract and normalize selected choice label from decision answer."""
    if hasattr(answer, "choice"):
        return _normalize_agent_name(answer.choice)
    if hasattr(answer, "selected_option"):
        return _normalize_agent_name(answer.selected_option)
    if isinstance(answer, dict):
        val = answer.get("choice") or answer.get("selected_option") or ""
        return _normalize_agent_name(val)
    return _normalize_agent_name(str(answer or ""))


def _extract_clef_confidence(answer: Any) -> float:
    """Safely extract calibrated confidence float from decision answer."""
    if hasattr(answer, "confidence"):
        try:
            return float(answer.confidence)
        except (ValueError, TypeError):
            pass
    if isinstance(answer, dict) and "confidence" in answer:
        try:
            return float(answer["confidence"])
        except (ValueError, TypeError):
            pass
    return 0.0


async def _route_with_clef(
    user_input: str,
    candidates: dict[str, AgentConfig],
    caller_agent_name: str,
    system_prompt: str = "",
    decision_client: DecisionClient | None = None,
    config: RunnableConfig | None = None,
) -> ChoiceAnswer:
    """Evaluate candidate agents via Clef-flash decision engine."""
    if not candidates:
        raise RuntimeError("No candidate agents are available for routing.")

    client = _resolve_decision_client(decision_client, config)
    if client is None:
        raise DecisionError("No decision client configured or available for Clef routing.")

    criteria = {name: _agent_description(cfg) for name, cfg in candidates.items()}
    instructions = (
        f"Select the most appropriate agent to handle the user request. "
        f"Candidates available: {', '.join(candidates.keys())}."
    )

    if hasattr(client, "evaluate_choice") and callable(client.evaluate_choice):
        return await client.evaluate_choice(
            state=user_input,
            instructions=instructions,
            criteria=criteria,
        )

    # Duck-typed fallback for evaluate()
    q = ChoiceQuestion(instructions=instructions, criteria=criteria)
    result = await client.evaluate(
        state=user_input,
        questions={"choice_q": q},
    )
    ans = result.answers.get("choice_q")
    if isinstance(ans, ChoiceAnswer):
        return ans
    if isinstance(ans, dict):
        return ChoiceAnswer.model_validate(ans)
    if isinstance(ans, str):
        return ChoiceAnswer(choice=ans, confidence=1.0)
    raise TypeError(f"Unexpected answer type from decision client: {type(ans).__name__}")


async def _route_agent_decision(
    *,
    user_input: str,
    candidates: dict[str, AgentConfig],
    router_prompt_template: str,
    caller_agent_name: str,
    provider: str | None = None,
    model: str | None = None,
    prompt_variables: dict[str, str] | None = None,
    config: RunnableConfig | None = None,
    mode: str = "cascade",
    confidence_threshold: float = 0.75,
    decision_client: DecisionClient | None = None,
    context: WorkflowContext | None = None,
    agent_config: AgentConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    """Dispatch routing decision according to configured mode (llm_only, clef_only, shadow, cascade)."""
    normalized_mode = str(mode).strip().lower() if isinstance(mode, str) else "cascade"
    if normalized_mode not in _ALLOWED_ROUTING_MODES:
        logger.warning("Unknown routing mode '%s', falling back to 'cascade'.", mode)
        normalized_mode = "cascade"
    mode = normalized_mode

    # 1. Mode: llm_only
    if mode == "llm_only":
        t0 = time.perf_counter()
        llm_selected = await _route_agent_name(
            user_input=user_input,
            candidates=candidates,
            router_prompt_template=router_prompt_template,
            caller_agent_name=caller_agent_name,
            provider=provider,
            model=model,
            prompt_variables=prompt_variables,
            config=config,
            context=context,
            agent_config=agent_config,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        telemetry = {
            "mode": "llm_only",
            "source": "llm",
            "selected_agent": llm_selected,
            "llm_latency_ms": latency,
            "clef_latency_ms": None,
            "latency_delta_ms": None,
            "clef_confidence": None,
            "confidence_threshold": confidence_threshold,
            "agreement": None,
            "cascade_triggered": False,
            "cascade_reason": None,
            "error": None,
        }
        return llm_selected, telemetry

    # 2. Mode: clef_only
    if mode == "clef_only":
        t0 = time.perf_counter()
        try:
            clef_answer = await _route_with_clef(
                user_input=user_input,
                candidates=candidates,
                caller_agent_name=caller_agent_name,
                system_prompt=router_prompt_template,
                decision_client=decision_client,
                config=config,
            )
            latency = (time.perf_counter() - t0) * 1000.0
            selected = _extract_clef_choice(clef_answer)
            confidence = _extract_clef_confidence(clef_answer)

            if selected in candidates:
                telemetry = {
                    "mode": "clef_only",
                    "source": "clef",
                    "selected_agent": selected,
                    "clef_confidence": confidence,
                    "confidence_threshold": confidence_threshold,
                    "clef_latency_ms": latency,
                    "llm_latency_ms": None,
                    "latency_delta_ms": None,
                    "agreement": None,
                    "cascade_triggered": False,
                    "cascade_reason": None,
                    "error": None,
                }
                return selected, telemetry

            # Clef selected invalid candidate
            logger.warning("clef_only selected invalid candidate '%s', falling back", selected)
            telemetry = {
                "mode": "clef_only",
                "source": "fallback",
                "selected_agent": "",
                "clef_confidence": confidence,
                "confidence_threshold": confidence_threshold,
                "clef_latency_ms": latency,
                "llm_latency_ms": None,
                "latency_delta_ms": None,
                "agreement": None,
                "cascade_triggered": False,
                "cascade_reason": f"invalid_candidate ({selected})",
                "error": f"Invalid candidate '{selected}' selected by Clef",
            }
            return "", telemetry
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            logger.warning("clef_only routing failed: %s", exc)
            telemetry = {
                "mode": "clef_only",
                "source": "fallback",
                "selected_agent": "",
                "clef_confidence": None,
                "confidence_threshold": confidence_threshold,
                "clef_latency_ms": latency,
                "llm_latency_ms": None,
                "latency_delta_ms": None,
                "agreement": None,
                "cascade_triggered": False,
                "cascade_reason": "clef_error",
                "error": str(exc),
            }
            return "", telemetry

    # 3. Mode: shadow
    if mode == "shadow":
        async def _run_clef():
            t = time.perf_counter()
            try:
                res = await _route_with_clef(
                    user_input=user_input,
                    candidates=candidates,
                    caller_agent_name=caller_agent_name,
                    system_prompt=router_prompt_template,
                    decision_client=decision_client,
                    config=config,
                )
                return res, (time.perf_counter() - t) * 1000.0, None
            except Exception as e:
                return None, (time.perf_counter() - t) * 1000.0, e

        async def _run_llm():
            t = time.perf_counter()
            try:
                res = await _route_agent_name(
                    user_input=user_input,
                    candidates=candidates,
                    router_prompt_template=router_prompt_template,
                    caller_agent_name=caller_agent_name,
                    provider=provider,
                    model=model,
                    prompt_variables=prompt_variables,
                    config=config,
                    context=context,
                    agent_config=agent_config,
                )
                return res, (time.perf_counter() - t) * 1000.0, None
            except Exception as e:
                return None, (time.perf_counter() - t) * 1000.0, e

        (clef_res, clef_lat, clef_err), (llm_res, llm_lat, llm_err) = await asyncio.gather(
            _run_clef(),
            _run_llm(),
        )

        clef_agent = _extract_clef_choice(clef_res) if clef_res is not None else ""
        clef_conf = _extract_clef_confidence(clef_res) if clef_res is not None else None

        llm_agent = _normalize_agent_name(llm_res) if llm_res is not None else ""

        agreement = (clef_agent == llm_agent) if (clef_agent and llm_agent) else False
        latency_delta = llm_lat - clef_lat

        telemetry = {
            "mode": "shadow",
            "source": "llm",
            "selected_agent": llm_agent,
            "llm_agent": llm_agent,
            "clef_agent": clef_agent,
            "clef_confidence": clef_conf,
            "confidence_threshold": confidence_threshold,
            "agreement": agreement,
            "clef_latency_ms": clef_lat,
            "llm_latency_ms": llm_lat,
            "latency_delta_ms": latency_delta,
            "cascade_triggered": False,
            "cascade_reason": None,
            "clef_error": str(clef_err) if clef_err else None,
            "llm_error": str(llm_err) if llm_err else None,
            "error": str(llm_err) if llm_err else (str(clef_err) if clef_err else None),
        }
        logger.info(
            "Router shadow telemetry: llm=%s, clef=%s (conf=%s), agreement=%s, delta=%.1fms",
            llm_agent,
            clef_agent,
            clef_conf,
            agreement,
            latency_delta,
        )
        return llm_agent, telemetry

    # 4. Mode: cascade (default)
    t0 = time.perf_counter()
    clef_decision = None
    clef_error: Exception | None = None
    try:
        clef_decision = await _route_with_clef(
            user_input=user_input,
            candidates=candidates,
            caller_agent_name=caller_agent_name,
            system_prompt=router_prompt_template,
            decision_client=decision_client,
            config=config,
        )
    except Exception as exc:
        clef_error = exc

    clef_latency = (time.perf_counter() - t0) * 1000.0

    clef_agent = _extract_clef_choice(clef_decision) if clef_decision is not None else ""
    clef_confidence = _extract_clef_confidence(clef_decision) if clef_decision is not None else 0.0

    # Fast-path condition:
    # 1. Clef succeeded without error
    # 2. confidence >= confidence_threshold
    # 3. selected agent is an eligible candidate
    if (
        clef_decision is not None
        and clef_confidence >= confidence_threshold
        and clef_agent in candidates
    ):
        logger.info(
            "Router cascade fast-path hit: agent='%s', conf=%.2f >= threshold=%.2f",
            clef_agent,
            clef_confidence,
            confidence_threshold,
        )
        telemetry = {
            "mode": "cascade",
            "source": "clef",
            "selected_agent": clef_agent,
            "clef_confidence": clef_confidence,
            "confidence_threshold": confidence_threshold,
            "clef_latency_ms": clef_latency,
            "llm_latency_ms": None,
            "latency_delta_ms": None,
            "agreement": None,
            "cascade_triggered": False,
            "cascade_reason": None,
            "error": None,
        }
        return clef_agent, telemetry

    # Cascade to LLM:
    if clef_error:
        cascade_reason = f"clef_error: {clef_error}"
    elif clef_decision is not None and clef_agent not in candidates:
        cascade_reason = f"invalid_candidate ({clef_agent})"
    else:
        cascade_reason = f"low_confidence ({clef_confidence:.2f} < {confidence_threshold:.2f})"

    logger.info("Router cascade triggered: %s", cascade_reason)

    t_llm = time.perf_counter()
    llm_selected = await _route_agent_name(
        user_input=user_input,
        candidates=candidates,
        router_prompt_template=router_prompt_template,
        caller_agent_name=caller_agent_name,
        provider=provider,
        model=model,
        prompt_variables=prompt_variables,
        config=config,
        context=context,
        agent_config=agent_config,
    )
    llm_latency = (time.perf_counter() - t_llm) * 1000.0

    telemetry = {
        "mode": "cascade",
        "source": "llm",
        "selected_agent": llm_selected,
        "clef_confidence": clef_confidence if clef_decision is not None else None,
        "confidence_threshold": confidence_threshold,
        "clef_latency_ms": clef_latency,
        "llm_latency_ms": llm_latency,
        "latency_delta_ms": None,
        "agreement": None,
        "cascade_triggered": True,
        "cascade_reason": cascade_reason,
        "error": str(clef_error) if clef_error else None,
    }
    return llm_selected, telemetry


def _select_fallback_agent(candidates: dict[str, AgentConfig], catalog: Any) -> AgentConfig | None:
    # Try candidates first
    for agent in candidates.values():
        if agent.graph_type != ROUTER_GRAPH_TYPE:
            return agent

    # Try default agent
    default_agent = catalog.get_agent(DEFAULT_AGENT_NAME)
    if default_agent is not None and default_agent.graph_type != ROUTER_GRAPH_TYPE:
        return default_agent

    # Last resort: first non-router agent from catalog
    for agent in catalog.list_agents():
        if agent.graph_type != ROUTER_GRAPH_TYPE:
            return agent

    return None


def _resolve_target_workflow(target_agent: AgentConfig | None) -> str:
    if target_agent is not None:
        workflow_name = safe_str_strip(target_agent.graph_type)
        if workflow_name and workflow_name != ROUTER_GRAPH_TYPE:
            if GraphRegistry.is_registered(workflow_name):
                return workflow_name

    workflows = GraphRegistry.routeable_workflows()
    if not workflows:
        raise RuntimeError("No routeable workflows are registered.")
    return _default_workflow(workflows)


def _build_target_context(runtime_context: WorkflowContext, target_agent: AgentConfig | None):
    if target_agent is None:
        return runtime_context

    workspace = runtime_context.get_workspace()
    allowed_tool_names = target_agent.tools if target_agent.tools else None
    return make_context(
        workspace=workspace,
        max_context_tokens=target_agent.max_context_tokens,
        agent_name=target_agent.name,
        allowed_tool_names=allowed_tool_names,
        allowed_skill_names=runtime_context.get_allowed_skill_names(),
        provider=runtime_context.get_provider(),
        model=runtime_context.get_model(),
        reasoning_effort=runtime_context.reasoning_effort,
    )


def _graph_accepts_context(graph: object) -> bool:
    context_schema = getattr(graph, "context_schema", Ellipsis)
    if context_schema is Ellipsis:
        return True
    return context_schema is not None


async def llm_call_router(
    state: RouterState,
    config: RunnableConfig,
    runtime: Runtime[WorkflowContext],
) -> dict:
    """Node to decide which agent/workflow to route to."""
    user_input = str(state["messages"][-1].content)

    catalog = get_catalog_service()
    ctx = runtime.context

    caller_agent_name = ctx.get_agent_name()

    caller_agent = catalog.get_agent(caller_agent_name)
    if caller_agent is None:
        raise RuntimeError(f"Router agent '{caller_agent_name}' not found in catalog.")
    candidates = _build_candidate_agents(caller_agent_name, catalog)
    provider = ctx.get_provider() or caller_agent.provider
    model = ctx.get_model() or caller_agent.model or None

    selected_agent_name = ""
    telemetry: dict[str, Any] = {}

    mode = _resolve_routing_mode(config)
    threshold = _resolve_confidence_threshold(config)

    if candidates:
        prompt_variables = await get_runtime_prompt_variable_values()
        selected_agent_name, telemetry = await _route_agent_decision(
            user_input=user_input,
            candidates=candidates,
            router_prompt_template=getattr(caller_agent, "system_prompt", ""),
            caller_agent_name=caller_agent_name,
            provider=provider,
            model=model,
            prompt_variables=prompt_variables,
            config=config,
            context=ctx,
            agent_config=caller_agent,
            mode=mode,
            confidence_threshold=threshold,
        )
    else:
        telemetry = {
            "mode": mode,
            "source": "fallback",
            "selected_agent": "",
            "confidence_threshold": threshold,
            "cascade_reason": "no_candidates",
            "cascade_triggered": False,
        }

    target_agent = candidates.get(selected_agent_name)
    if target_agent is None or target_agent.graph_type == ROUTER_GRAPH_TYPE:
        target_agent = _select_fallback_agent(candidates, catalog)
        telemetry["fallback_used"] = True
        telemetry["fallback_target"] = target_agent.name if target_agent else None
    else:
        telemetry["fallback_used"] = False

    logger.info(
        f"Router selected agent: {target_agent.name if target_agent else 'none'} "
        f"[mode={telemetry.get('mode', 'none')}, source={telemetry.get('source', 'none')}]"
    )
    return {
        "target_agent": target_agent,
        "routing_telemetry": telemetry,
    }


async def llm_call(
    state: RouterState,
    config: RunnableConfig,
    runtime: Runtime[WorkflowContext],
) -> dict:
    """Node to execute the selected workflow."""
    target_agent = state.get("target_agent")
    target_workflow = _resolve_target_workflow(target_agent)

    target_graph = GraphRegistry.get(target_workflow)

    invoke_kwargs = {"config": config}
    if _graph_accepts_context(target_graph):
        invoke_kwargs["context"] = _build_target_context(runtime.context, target_agent)

    result = await target_graph.ainvoke(
        {"messages": state["messages"]},
        **invoke_kwargs,
    )
    return {"messages": result["messages"]}


def build_router_graph() -> None:
    graph = StateGraph(RouterState, context_schema=WorkflowContext)
    graph.add_node("llm_call_router", llm_call_router)
    graph.add_node("llm_call", llm_call)
    graph.add_edge(START, "llm_call_router")
    graph.add_edge("llm_call_router", "llm_call")
    graph.add_edge("llm_call", END)

    GraphRegistry.register(
        "router",
        graph.compile(checkpointer=get_checkpointer()),
        description="internal workflow router",
        routeable=False,
    )
