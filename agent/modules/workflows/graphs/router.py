from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from agent.modules.agents import AgentConfig, get_catalog_service

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
from agent.modules.providers import get_chat_model
from agent.shared.infrastructure.parsing import safe_str_strip

logger = logging.getLogger(__name__)


class RouterState(BaseState):
    """State for router graph with routing decision fields."""

    target_agent: AgentConfig | None = None


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

    try:
        return template.format(
            agent_options=agent_options,
            user_input=user_input,
            caller_agent_name=caller_agent_name,
        )
    except KeyError as exc:
        raise RuntimeError(
            f"Router agent '{caller_agent_name}' template references undefined placeholder: {exc}"
        ) from exc


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
    model: str | None = None,
) -> str:
    llm = get_chat_model(model=model)
    router_system = _build_router_system(
        user_input=user_input,
        candidates=candidates,
        router_prompt_template=router_prompt_template,
        caller_agent_name=caller_agent_name,
    )
    messages = [
        SystemMessage(content=router_system),
        HumanMessage(content=user_input),
    ]

    try:
        decision = await llm.with_structured_output(_RouteDecision).ainvoke(messages)
        return _normalize_agent_name(decision.selected_agent)
    except Exception as exc:
        logger.warning("Structured output failed, falling back to text parsing: %s", exc)
        response = await llm.ainvoke(messages)
        content = getattr(response, "content", "")
        return _normalize_agent_name(content if content else "")


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

    working_dir = runtime_context.get_working_dir()
    allowed_tool_names = target_agent.tools if target_agent.tools else None
    return make_context(
        working_dir=working_dir,
        max_context_tokens=target_agent.max_context_tokens,
        agent_name=target_agent.name,
        allowed_tool_names=allowed_tool_names,
        model=runtime_context.get_model(),
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
    candidates = _build_candidate_agents(caller_agent_name, catalog)
    model = ctx.get_model() or getattr(caller_agent, "model", "") or None

    selected_agent_name = ""
    if candidates:
        selected_agent_name = await _route_agent_name(
            user_input,
            candidates,
            router_prompt_template=getattr(caller_agent, "system_prompt", ""),
            caller_agent_name=caller_agent_name,
            model=model,
        )

    target_agent = candidates.get(selected_agent_name)
    if target_agent is None or target_agent.graph_type == ROUTER_GRAPH_TYPE:
        target_agent = _select_fallback_agent(candidates, catalog)

    logger.info(f"Router selected agent: {target_agent.name if target_agent else 'none'}")
    return {"target_agent": target_agent}


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
