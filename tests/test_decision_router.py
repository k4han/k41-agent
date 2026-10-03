"""Comprehensive unit tests for router.py decision model integration.

Tests all 4 routing modes:
1. llm_only: legacy chat LLM structured output
2. clef_only: direct Clef-flash routing with fallback on error/invalid candidate
3. shadow: parallel dual-run via asyncio.gather with telemetry and agreement rate
4. cascade: fast-path routing on confidence >= threshold, cascading on low confidence/error
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.modules.agents.models import AgentConfig
from agent.modules.decisions import (
    ChoiceAnswer,
    DecisionAPIError,
    DecisionTimeoutError,
    MockDecisionClient,
)
import agent.modules.workflows.graphs.router as router_module
from agent.modules.workflows.registry import GraphRegistry
from agent.modules.workflows.run_config import WorkflowContext, make_context


class _FakeChatModel:
    def __init__(self, selected_agent: str, captured: dict[str, Any] | None = None, raise_on_structured: bool = False):
        self._selected_agent = selected_agent
        self._captured = captured if captured is not None else {}
        self._raise_on_structured = raise_on_structured
        self.call_count = 0

    def with_structured_output(self, schema: Any):
        self._captured["schema"] = schema
        if self._raise_on_structured:
            raise RuntimeError("Simulated structured output failure")
        return self

    async def ainvoke(self, messages: Any, config: Any = None):
        self.call_count += 1
        self._captured["messages"] = messages
        self._captured["config"] = config
        if hasattr(self, "_raise_on_structured") and self._raise_on_structured:
            # Fallback text mode returns an AIMessage with the selected agent name
            return AIMessage(content=self._selected_agent)
        return SimpleNamespace(selected_agent=self._selected_agent)


def _fake_resolved_model(model: _FakeChatModel):
    return SimpleNamespace(
        model=model,
        provider_name="fake-provider",
        provider_type="openai_compatible",
        model_name="fake-model",
    )


class _FakeCatalog:
    def __init__(
        self,
        *,
        agents: dict[str, AgentConfig],
        callable_map: dict[str, list[str]] | None = None,
    ):
        self._agents = dict(agents)
        self._callable_map = callable_map or {}

    def get_agent(self, name: str):
        return self._agents.get(name)

    def list_agents(self):
        return list(self._agents.values())

    def get_callable_agents(self, for_agent_name: str):
        return list(self._callable_map.get(for_agent_name, []))


def _make_agent(
    *,
    name: str,
    graph_type: str,
    description: str = "",
    provider: str = "default",
    model: str = "",
    tools: list[str] | None = None,
    max_context_tokens: int = 50_000,
    system_prompt: str = (
        "You are router {caller_agent_name}.\n"
        "Candidates:\n{agent_options}\n\n"
        "User request:\n{user_input}\n\n"
        "Return only selected_agent."
    ),
) -> AgentConfig:
    return AgentConfig(
        name=name,
        display_name=name.title(),
        description=description,
        graph_type=graph_type,
        provider=provider,
        model=model,
        tools=list(tools or []),
        sub_agents=None,
        max_context_tokens=max_context_tokens,
        system_prompt=system_prompt,
    )


def _runtime_context(**overrides) -> WorkflowContext:
    defaults = {
        "agent_name": "orchestrator",
        "working_dir": "D:/repo",
        "max_context_tokens": 50_000,
        "allowed_tool_names": [],
    }
    defaults.update(overrides)
    return make_context(**defaults)


class _FakeGraph:
    def __init__(self, result_text: str):
        self.result_text = result_text
        self.calls: list[dict[str, object]] = []

    async def ainvoke(self, state, *, config, context=None):
        self.calls.append(
            {
                "state": state,
                "config": config,
                "context": context,
            }
        )
        return {"messages": [AIMessage(content=self.result_text)]}


@pytest.fixture
def isolated_router_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())

    # Register standard test graphs
    GraphRegistry.register("research_chain", _FakeGraph("research-result"), description="research")
    GraphRegistry.register("planner_chain", _FakeGraph("planner-result"), description="planning")
    GraphRegistry.register("react_agent", _FakeGraph("fallback-result"), description="general")

    catalog = _FakeCatalog(
        agents={
            "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
            "researcher": _make_agent(
                name="researcher",
                graph_type="research_chain",
                description="Web research specialist",
            ),
            "planner": _make_agent(
                name="planner",
                graph_type="planner_chain",
                description="Project planning specialist",
            ),
            "default": _make_agent(name="default", graph_type="react_agent"),
        },
        callable_map={"orchestrator": ["researcher", "planner"]},
    )
    monkeypatch.setattr(router_module, "get_catalog_service", lambda: catalog)


# --- 1. Mode: llm_only Tests ---


@pytest.mark.asyncio
async def test_route_llm_only_uses_chat_llm_and_ignores_clef(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("researcher")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(canned_answers={"*": "planner"})

    state = {"messages": [HumanMessage(content="Search the web for docs")]}
    config = {
        "configurable": {
            "router_mode": "llm_only",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "researcher"
    assert chat_model.call_count == 1
    assert len(mock_clef.recorded_calls) == 0  # Clef was never invoked

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "llm_only"
    assert telemetry["source"] == "llm"
    assert telemetry["selected_agent"] == "researcher"
    assert telemetry["llm_latency_ms"] is not None
    assert telemetry["clef_latency_ms"] is None
    assert telemetry["agreement"] is None


@pytest.mark.asyncio
async def test_route_llm_only_recovers_via_raw_text_fallback(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("planner", raise_on_structured=True)
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    state = {"messages": [HumanMessage(content="Create a project plan")]}
    config = {"configurable": {"router_mode": "llm_only"}}
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "planner"
    assert res["routing_telemetry"]["source"] == "llm"


# --- 2. Mode: clef_only Tests ---


@pytest.mark.asyncio
async def test_route_clef_only_routes_directly_without_calling_llm(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    llm_called = False

    def _fail_llm(**kw):
        nonlocal llm_called
        llm_called = True
        raise AssertionError("LLM should not be called in clef_only mode")

    monkeypatch.setattr(router_module, "get_resolved_chat_model", _fail_llm)

    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.92)}
    )

    state = {"messages": [HumanMessage(content="Investigate market trends")]}
    config = {
        "configurable": {
            "router_mode": "clef_only",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "researcher"
    assert not llm_called
    assert len(mock_clef.recorded_calls) == 1

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "clef_only"
    assert telemetry["source"] == "clef"
    assert telemetry["selected_agent"] == "researcher"
    assert telemetry["clef_confidence"] == 0.92
    assert telemetry["clef_latency_ms"] is not None
    assert telemetry["llm_latency_ms"] is None


@pytest.mark.asyncio
async def test_route_clef_only_falls_back_when_clef_raises_error(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    mock_clef = MockDecisionClient(canned_answers={"*": DecisionTimeoutError("Inference timed out")})

    state = {"messages": [HumanMessage(content="Any task")]}
    config = {
        "configurable": {
            "router_mode": "clef_only",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # Falls back to first non-router candidate (researcher)
    assert res["target_agent"].name == "researcher"
    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "clef_only"
    assert telemetry["source"] == "fallback"
    assert telemetry["fallback_used"] is True
    assert telemetry["error"] == "Inference timed out"


@pytest.mark.asyncio
async def test_route_clef_only_falls_back_when_clef_selects_invalid_candidate(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    mock_clef = MockDecisionClient(canned_answers={"*": ChoiceAnswer(choice="unknown_ghost_agent", confidence=0.88)})

    state = {"messages": [HumanMessage(content="Any task")]}
    config = {
        "configurable": {
            "router_mode": "clef_only",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "researcher"
    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "clef_only"
    assert telemetry["source"] == "fallback"
    assert telemetry["fallback_used"] is True


# --- 3. Mode: shadow Tests ---


@pytest.mark.asyncio
async def test_route_shadow_runs_parallel_and_primary_flow_uses_llm(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    # Clef selects a different agent to test agreement=False
    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.91)},
        latency_ms=20.0,
    )

    state = {"messages": [HumanMessage(content="Compare architectures")]}
    config = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # Primary flow MUST use LLM decision (planner), not Clef (researcher)
    assert res["target_agent"].name == "planner"
    assert chat_model.call_count == 1
    assert len(mock_clef.recorded_calls) == 1

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "shadow"
    assert telemetry["source"] == "llm"
    assert telemetry["selected_agent"] == "planner"
    assert telemetry["llm_agent"] == "planner"
    assert telemetry["clef_agent"] == "researcher"
    assert telemetry["clef_confidence"] == 0.91
    assert telemetry["agreement"] is False
    assert telemetry["latency_delta_ms"] is not None
    assert telemetry["clef_error"] is None
    assert telemetry["llm_error"] is None


@pytest.mark.asyncio
async def test_route_shadow_logs_agreement_when_both_agree(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("researcher")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.95)}
    )

    state = {"messages": [HumanMessage(content="Search for Python tutorials")]}
    config = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "researcher"
    telemetry = res["routing_telemetry"]
    assert telemetry["agreement"] is True
    assert telemetry["llm_agent"] == "researcher"
    assert telemetry["clef_agent"] == "researcher"


@pytest.mark.asyncio
async def test_route_shadow_recovers_if_clef_raises_exception(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(canned_answers={"*": DecisionAPIError("Network connection reset", status_code=502)})

    state = {"messages": [HumanMessage(content="Schedule work")]}
    config = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # Primary flow succeeds despite Clef error
    assert res["target_agent"].name == "planner"
    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "shadow"
    assert telemetry["clef_error"] is not None
    assert "Network connection reset" in telemetry["clef_error"]
    assert telemetry["agreement"] is False


# --- 4. Mode: cascade Tests ---


@pytest.mark.asyncio
async def test_route_cascade_high_confidence_routes_immediately_fast_path(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    llm_called = False

    def _fail_llm(**kw):
        nonlocal llm_called
        llm_called = True
        raise AssertionError("LLM should not be called when Clef confidence >= threshold")

    monkeypatch.setattr(router_module, "get_resolved_chat_model", _fail_llm)

    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.85)}
    )

    state = {"messages": [HumanMessage(content="Find latest AI research")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": 0.75,
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "researcher"
    assert not llm_called

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "cascade"
    assert telemetry["source"] == "clef"
    assert telemetry["selected_agent"] == "researcher"
    assert telemetry["clef_confidence"] == 0.85
    assert telemetry["cascade_triggered"] is False
    assert telemetry["cascade_reason"] is None
    assert telemetry["llm_latency_ms"] is None


@pytest.mark.asyncio
async def test_route_cascade_low_confidence_triggers_cascade_to_llm(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    # Low confidence 0.55 < threshold 0.75
    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.55)}
    )

    state = {"messages": [HumanMessage(content="Ambiguous prompt")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": 0.75,
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # Cascaded to LLM, which selected planner
    assert res["target_agent"].name == "planner"
    assert chat_model.call_count == 1
    assert len(mock_clef.recorded_calls) == 1

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "cascade"
    assert telemetry["source"] == "llm"
    assert telemetry["selected_agent"] == "planner"
    assert telemetry["cascade_triggered"] is True
    assert "low_confidence" in telemetry["cascade_reason"]
    assert telemetry["clef_latency_ms"] is not None
    assert telemetry["llm_latency_ms"] is not None


@pytest.mark.asyncio
async def test_route_cascade_clef_error_gracefully_cascades_to_llm(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("researcher")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(canned_answers={"*": DecisionTimeoutError("Gateway timeout")})

    state = {"messages": [HumanMessage(content="Research prompt")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "researcher"
    assert chat_model.call_count == 1

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "cascade"
    assert telemetry["source"] == "llm"
    assert telemetry["cascade_triggered"] is True
    assert "clef_error" in telemetry["cascade_reason"]


@pytest.mark.asyncio
async def test_route_cascade_invalid_candidate_cascades_to_llm(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(canned_answers={"*": ChoiceAnswer(choice="hallucinated_agent", confidence=0.99)})

    state = {"messages": [HumanMessage(content="Plan work")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "planner"
    telemetry = res["routing_telemetry"]
    assert telemetry["cascade_triggered"] is True
    assert "invalid_candidate" in telemetry["cascade_reason"]


# --- 5. End-to-End Workflow Graph Execution with Telemetry in State ---


@pytest.mark.asyncio
async def test_full_router_workflow_populates_state_telemetry(monkeypatch: pytest.MonkeyPatch, isolated_router_env):
    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.90)}
    )

    state = {"messages": [HumanMessage(content="Deep research")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    # Step 1: router node
    router_step = await router_module.llm_call_router(state, config, runtime)
    assert router_step["target_agent"].name == "researcher"
    assert router_step["routing_telemetry"]["source"] == "clef"

    # Merge into state
    state.update(router_step)

    # Step 2: execution node
    exec_step = await router_module.llm_call(state, config, runtime)
    assert exec_step["messages"][0].content == "research-result"
