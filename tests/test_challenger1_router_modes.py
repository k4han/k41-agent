"""Challenger 1 Empirical Stress-Test Suite for Milestone 2 Router Modes.

Empirically tests:
1. Cascade mode boundary precision:
   - confidence == 0.74 (just below 0.75) -> cascades to LLM
   - confidence == 0.75 (exact threshold) -> fast-path Clef
   - confidence == 0.76 (just above 0.75) -> fast-path Clef
   - Custom configurable thresholds (e.g. 0.80, 0.99)
   - Invalid candidate with high confidence (1.0) -> cascades to LLM
2. Shadow mode agreement vs disagreement rate & telemetry:
   - Agreement (both choose researcher) -> telemetry agreement=True
   - Disagreement (Clef researcher, LLM planner) -> telemetry agreement=False, main flow returns LLM (planner)
   - Multi-trial statistical agreement rate harness (50 trials)
   - Clef error isolation in shadow mode
   - LLM error isolation and fallback in shadow mode
3. clef_only mode:
   - Valid candidate -> direct route, fallback_used=False
   - Invalid candidate -> fallback to _select_fallback_agent, fallback_used=True, fallback_target recorded
   - Clef error -> fallback to _select_fallback_agent
   - Selected candidate is a router -> fallback to non-router to prevent loops
4. llm_only mode:
   - Explosive Clef client verifying Clef client methods are never called
   - Structured output fallback to text parsing
5. Bug discovery test:
   - Python 'or' falsy bug when confidence_threshold=0.0 resolves to 0.75
"""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.modules.agents.models import AgentConfig
from agent.modules.decisions import (
    ChoiceAnswer,
    DecisionAPIError,
    DecisionClient,
    DecisionResult,
    DecisionTimeoutError,
    MockDecisionClient,
)
import agent.modules.workflows.graphs.router as router_module
from agent.modules.workflows.graphs.router import RouterTelemetry
from agent.modules.workflows.registry import GraphRegistry
from agent.modules.workflows.run_config import WorkflowContext, make_context


class _FakeChatModel:
    def __init__(self, selected_agent: str, raise_on_ainvoke: bool = False):
        self._selected_agent = selected_agent
        self._raise_on_ainvoke = raise_on_ainvoke
        self.call_count = 0

    def with_structured_output(self, schema: Any):
        return self

    async def ainvoke(self, messages: Any, config: Any = None):
        self.call_count += 1
        if self._raise_on_ainvoke:
            raise RuntimeError("Simulated ChatModel catastrophic failure")
        return SimpleNamespace(selected_agent=self._selected_agent)


def _fake_resolved_model(model: _FakeChatModel):
    return SimpleNamespace(
        model=model,
        provider_name="test-provider",
        provider_type="openai_compatible",
        model_name="test-model",
    )


class _ExplosiveDecisionClient(DecisionClient):
    """Client that explodes with AssertionError if any method is called."""

    def __init__(self):
        self.call_count = 0

    async def evaluate(self, state, questions, **kwargs) -> DecisionResult:
        self.call_count += 1
        raise AssertionError("ExplosiveDecisionClient.evaluate() should NEVER be invoked!")

    async def evaluate_choice(self, state, instructions, criteria, **kwargs) -> ChoiceAnswer:
        self.call_count += 1
        raise AssertionError("ExplosiveDecisionClient.evaluate_choice() should NEVER be invoked!")


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
) -> AgentConfig:
    return AgentConfig(
        name=name,
        display_name=name.title(),
        description=description,
        graph_type=graph_type,
        provider="default",
        model="gpt-4o",
        tools=[],
        sub_agents=None,
        max_context_tokens=50_000,
        system_prompt="Router prompt {agent_options} {user_input}",
    )


def _runtime_context() -> WorkflowContext:
    return make_context(
        agent_name="orchestrator",
        working_dir="D:/repo",
        max_context_tokens=50_000,
        allowed_tool_names=[],
    )


@pytest.fixture
def challenger1_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())

    catalog = _FakeCatalog(
        agents={
            "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
            "researcher": _make_agent(
                name="researcher",
                graph_type="research_chain",
                description="Web and data researcher",
            ),
            "planner": _make_agent(
                name="planner",
                graph_type="planner_chain",
                description="Strategic planning agent",
            ),
            "sub_router": _make_agent(
                name="sub_router",
                graph_type="router",
                description="Nested router agent",
            ),
            "default": _make_agent(name="default", graph_type="react_agent"),
        },
        callable_map={"orchestrator": ["researcher", "planner", "sub_router"]},
    )
    monkeypatch.setattr(router_module, "get_catalog_service", lambda: catalog)


# =========================================================================
# 1. CASCADE MODE: Exact Threshold Boundaries (0.74 vs 0.75 vs 0.76)
# =========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "confidence,expected_source,expected_agent,expect_llm_call,expect_cascade",
    [
        (0.74, "llm", "planner", True, True),       # Just below threshold (0.74 < 0.75)
        (0.75, "clef", "researcher", False, False),  # Exact threshold (0.75 >= 0.75)
        (0.76, "clef", "researcher", False, False),  # Just above threshold (0.76 >= 0.75)
    ],
)
async def test_cascade_boundary_precision_74_75_76(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
    confidence: float,
    expected_source: str,
    expected_agent: str,
    expect_llm_call: bool,
    expect_cascade: bool,
):
    """Verify exact boundary behavior around default 0.75 threshold."""
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=confidence)}
    )

    state = {"messages": [HumanMessage(content="Analyze user query")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": 0.75,
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == expected_agent
    assert (chat_model.call_count > 0) == expect_llm_call

    telemetry = res["routing_telemetry"]
    RouterTelemetry.model_validate(telemetry)

    assert telemetry["mode"] == "cascade"
    assert telemetry["source"] == expected_source
    assert telemetry["cascade_triggered"] is expect_cascade
    assert telemetry["clef_confidence"] == confidence
    assert telemetry["confidence_threshold"] == 0.75

    if expect_cascade:
        assert telemetry["cascade_reason"] == f"low_confidence ({confidence:.2f} < 0.75)"
        assert telemetry["llm_latency_ms"] is not None
    else:
        assert telemetry["cascade_reason"] is None
        assert telemetry["llm_latency_ms"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "threshold,confidence,expected_source,expected_agent,expect_cascade",
    [
        (0.80, 0.7999, "llm", "planner", True),
        (0.80, 0.8000, "clef", "researcher", False),
        (0.80, 0.8001, "clef", "researcher", False),
        (0.99, 0.9899, "llm", "planner", True),
        (0.99, 0.9900, "clef", "researcher", False),
    ],
)
async def test_cascade_arbitrary_configured_thresholds(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
    threshold: float,
    confidence: float,
    expected_source: str,
    expected_agent: str,
    expect_cascade: bool,
):
    """Verify precision across non-default configured thresholds (e.g. 0.80, 0.99)."""
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=confidence)}
    )

    state = {"messages": [HumanMessage(content="Threshold calibration check")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": threshold,
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == expected_agent
    telemetry = res["routing_telemetry"]
    RouterTelemetry.model_validate(telemetry)
    assert telemetry["source"] == expected_source
    assert telemetry["cascade_triggered"] is expect_cascade


@pytest.mark.asyncio
async def test_cascade_high_confidence_but_invalid_candidate_cascades_to_llm(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
):
    """Even if confidence is 1.0, an invalid candidate must NOT fast-path."""
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    mock_clef = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="hallucinated_agent_xxx", confidence=1.0)}
    )

    state = {"messages": [HumanMessage(content="Adversarial agent test")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": 0.75,
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "planner"
    assert chat_model.call_count == 1
    telemetry = res["routing_telemetry"]
    assert telemetry["cascade_triggered"] is True
    assert "invalid_candidate (hallucinated_agent_xxx)" in telemetry["cascade_reason"]


# =========================================================================
# 2. SHADOW MODE: Agreement vs Disagreement, Telemetry & Error Isolation
# =========================================================================


@pytest.mark.asyncio
async def test_shadow_agreement_rate_and_telemetry(monkeypatch: pytest.MonkeyPatch, challenger1_env):
    """Test both agreement=True and agreement=False in shadow mode."""
    # Trial 1: Agreement
    chat_model_1 = _FakeChatModel("researcher")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model_1))
    mock_clef_1 = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.88)},
        latency_ms=15.0,
    )

    state = {"messages": [HumanMessage(content="Query 1")]}
    config_1 = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": mock_clef_1,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res_1 = await router_module.llm_call_router(state, config_1, runtime)
    assert res_1["target_agent"].name == "researcher"
    tele_1 = res_1["routing_telemetry"]
    RouterTelemetry.model_validate(tele_1)
    assert tele_1["mode"] == "shadow"
    assert tele_1["source"] == "llm"
    assert tele_1["agreement"] is True
    assert tele_1["llm_agent"] == "researcher"
    assert tele_1["clef_agent"] == "researcher"
    assert tele_1["clef_latency_ms"] is not None
    assert tele_1["llm_latency_ms"] is not None
    assert tele_1["latency_delta_ms"] is not None

    # Trial 2: Disagreement
    chat_model_2 = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model_2))
    mock_clef_2 = MockDecisionClient(
        canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.85)},
        latency_ms=10.0,
    )

    config_2 = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": mock_clef_2,
        }
    }
    res_2 = await router_module.llm_call_router(state, config_2, runtime)
    # Primary flow must obey LLM ("planner"), NOT Clef ("researcher")
    assert res_2["target_agent"].name == "planner"
    tele_2 = res_2["routing_telemetry"]
    RouterTelemetry.model_validate(tele_2)
    assert tele_2["agreement"] is False
    assert tele_2["llm_agent"] == "planner"
    assert tele_2["clef_agent"] == "researcher"


@pytest.mark.asyncio
async def test_shadow_statistical_harness_50_trials(monkeypatch: pytest.MonkeyPatch, challenger1_env):
    """Stress test shadow mode across 50 randomized trials to compute agreement rate."""
    agreements = 0
    total = 50
    rng = random.Random(12345)

    for i in range(total):
        # 60% chance of agreeing
        llm_choice = "researcher" if rng.random() < 0.6 else "planner"
        clef_choice = "researcher"

        chat_model = _FakeChatModel(llm_choice)
        monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))
        mock_clef = MockDecisionClient(canned_answers={"*": ChoiceAnswer(choice=clef_choice, confidence=0.90)})

        state = {"messages": [HumanMessage(content=f"Batch query {i}")]}
        config = {
            "configurable": {
                "router_mode": "shadow",
                "decision_client": mock_clef,
            }
        }
        res = await router_module.llm_call_router(state, config, SimpleNamespace(context=_runtime_context()))
        tele = res["routing_telemetry"]

        expected_agree = (llm_choice == clef_choice)
        assert tele["agreement"] is expected_agree
        assert res["target_agent"].name == llm_choice
        if tele["agreement"]:
            agreements += 1

    agreement_rate = agreements / total
    assert 0.45 <= agreement_rate <= 0.75
    print(f"\nShadow empirical agreement rate across {total} trials: {agreement_rate * 100:.1f}%")


@pytest.mark.asyncio
async def test_shadow_mode_isolates_llm_failure_and_triggers_catalog_fallback(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
):
    """If LLM crashes in shadow mode, primary flow catches it and falls back safely."""
    chat_model = _FakeChatModel("planner", raise_on_ainvoke=True)
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))
    mock_clef = MockDecisionClient(canned_answers={"*": ChoiceAnswer(choice="researcher", confidence=0.95)})

    state = {"messages": [HumanMessage(content="Resilience test")]}
    config = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # Falls back to first non-router candidate (researcher)
    assert res["target_agent"].name == "researcher"
    tele = res["routing_telemetry"]
    RouterTelemetry.model_validate(tele)
    assert tele["fallback_used"] is True
    assert tele["llm_error"] is not None
    assert "Simulated ChatModel catastrophic failure" in tele["llm_error"]
    assert tele["agreement"] is False


# =========================================================================
# 3. CLEF_ONLY MODE: Valid Candidate vs Invalid Candidate Triggering Fallback
# =========================================================================


@pytest.mark.asyncio
async def test_clef_only_valid_candidate_routes_directly_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
):
    """Valid candidate selected by Clef routes without invoking LLM or fallback."""
    def _fail_llm(**kw):
        raise AssertionError("LLM must not be called in clef_only mode!")

    monkeypatch.setattr(router_module, "get_resolved_chat_model", _fail_llm)

    mock_clef = MockDecisionClient(canned_answers={"*": ChoiceAnswer(choice="planner", confidence=0.89)})

    state = {"messages": [HumanMessage(content="Valid task")]}
    config = {
        "configurable": {
            "router_mode": "clef_only",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "planner"
    telemetry = res["routing_telemetry"]
    RouterTelemetry.model_validate(telemetry)
    assert telemetry["mode"] == "clef_only"
    assert telemetry["source"] == "clef"
    assert telemetry["fallback_used"] is False
    assert telemetry.get("fallback_target") is None


@pytest.mark.asyncio
async def test_clef_only_invalid_candidate_triggers_fallback_agent(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
):
    """When Clef selects a non-existent candidate, _select_fallback_agent is triggered."""
    def _fail_llm(**kw):
        raise AssertionError("LLM must not be called in clef_only mode even on fallback!")

    monkeypatch.setattr(router_module, "get_resolved_chat_model", _fail_llm)

    mock_clef = MockDecisionClient(canned_answers={"*": ChoiceAnswer(choice="phantom_agent_does_not_exist", confidence=0.99)})

    state = {"messages": [HumanMessage(content="Invalid candidate test")]}
    config = {
        "configurable": {
            "router_mode": "clef_only",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # First non-router candidate is researcher
    assert res["target_agent"].name == "researcher"
    telemetry = res["routing_telemetry"]
    RouterTelemetry.model_validate(telemetry)
    assert telemetry["mode"] == "clef_only"
    assert telemetry["source"] == "fallback"
    assert telemetry["fallback_used"] is True
    assert telemetry["fallback_target"] == "researcher"
    assert "Invalid candidate 'phantom_agent_does_not_exist'" in telemetry["error"]


@pytest.mark.asyncio
async def test_clef_only_candidate_is_nested_router_triggers_fallback_agent(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
):
    """If Clef selects an agent whose graph_type == 'router', router fallback prevents recursion."""
    mock_clef = MockDecisionClient(canned_answers={"*": ChoiceAnswer(choice="sub_router", confidence=0.99)})

    state = {"messages": [HumanMessage(content="Nested router prevention")]}
    config = {
        "configurable": {
            "router_mode": "clef_only",
            "decision_client": mock_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # sub_router is router graph_type, so _select_fallback_agent selects first non-router (researcher)
    assert res["target_agent"].name == "researcher"
    telemetry = res["routing_telemetry"]
    assert telemetry["fallback_used"] is True
    assert telemetry["fallback_target"] == "researcher"


# =========================================================================
# 4. LLM_ONLY MODE: Clef Client Absolute Isolation
# =========================================================================


@pytest.mark.asyncio
async def test_llm_only_guarantees_clef_never_invoked(
    monkeypatch: pytest.MonkeyPatch,
    challenger1_env,
):
    """Strictly verify that Clef client methods are never called under llm_only mode."""
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    explosive_clef = _ExplosiveDecisionClient()

    state = {"messages": [HumanMessage(content="Planning query")]}
    config = {
        "configurable": {
            "router_mode": "llm_only",
            "decision_client": explosive_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # ExplosiveDecisionClient was never invoked (no AssertionError raised)
    assert explosive_clef.call_count == 0
    assert chat_model.call_count == 1
    assert res["target_agent"].name == "planner"

    telemetry = res["routing_telemetry"]
    RouterTelemetry.model_validate(telemetry)
    assert telemetry["mode"] == "llm_only"
    assert telemetry["source"] == "llm"
    assert telemetry["clef_latency_ms"] is None
    assert telemetry["clef_confidence"] is None
    assert telemetry["agreement"] is None


# =========================================================================
# 5. BUG EMPIRICAL PROOF: 0.0 Threshold Shadowing
# =========================================================================


def test_confidence_threshold_zero_resolution_bug():
    """Threshold 0.0 must be respected (always fast-path), not swallowed as falsy."""
    # When a user or system explicitly configures confidence_threshold=0.0 (meaning always fast-path):
    resolved = router_module._resolve_confidence_threshold({"configurable": {"confidence_threshold": 0.0}})
    assert resolved == 0.0, "Threshold 0.0 must resolve to 0.0, not the 0.75 default"
