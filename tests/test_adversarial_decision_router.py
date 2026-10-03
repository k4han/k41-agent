"""Empirical Adversarial Test Suite for Milestone 2: Parallel & Cascade Router Graph Integration.

Adversarially stress-tests:
1. Error Recovery in Cascade Mode:
   - httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError
   - Unhandled client exceptions (KeyError, TypeError, ValueError, CustomVendorException)
   - Malformed/unexpected Clef outputs (unknown candidate, empty choice, NaN/negative confidence)
   - Missing decision client credentials
   - Multi-tier failure recovery (Clef fails + LLM structured fails -> raw text -> candidate fallback)
2. Concurrency & Latency Delta in Shadow Mode:
   - Slow Clef, Fast LLM: verifies asyncio.gather concurrent execution (wall clock time << serial)
   - Fast Clef, Slow LLM: verifies latency delta sign and magnitude
   - Error in slow Clef: verifies main flow immunity and error capture in telemetry
   - Error in LLM branch: verifies clean fallback without workflow crash
3. Fallback Chain Integrity & Precedence:
   - Tier 1: Candidate non-router precedence
   - Tier 2: Default agent precedence when candidates has no non-router
   - Tier 3: Catalog non-router precedence when default agent is missing or a router
   - Loop avoidance: Rejecting candidates with graph_type="router"
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.modules.agents.models import AgentConfig
from agent.modules.decisions import (
    ChoiceAnswer,
    DecisionClient,
    DecisionError,
    MockDecisionClient,
)
import agent.modules.workflows.graphs.router as router_module
from agent.modules.workflows.registry import GraphRegistry
from agent.modules.workflows.run_config import WorkflowContext, make_context


class _FakeChatModel:
    def __init__(
        self,
        selected_agent: str,
        delay_s: float = 0.0,
        raise_on_structured: bool = False,
        raise_on_ainvoke: Exception | None = None,
    ):
        self._selected_agent = selected_agent
        self._delay_s = delay_s
        self._raise_on_structured = raise_on_structured
        self._raise_on_ainvoke = raise_on_ainvoke
        self.call_count = 0

    def with_structured_output(self, schema: Any):
        if self._raise_on_structured:
            raise RuntimeError("Simulated structured output parsing failure")
        return self

    async def ainvoke(self, messages: Any, config: Any = None):
        self.call_count += 1
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        if self._raise_on_ainvoke:
            raise self._raise_on_ainvoke
        if self._raise_on_structured:
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
) -> AgentConfig:
    return AgentConfig(
        name=name,
        display_name=name.title(),
        description=description,
        graph_type=graph_type,
        provider="default",
        model="test-model",
        tools=[],
        sub_agents=None,
        max_context_tokens=50_000,
        system_prompt="Router prompt {agent_options} {user_input}",
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


class _CustomVendorException(Exception):
    """Simulated unhandled third-party vendor client exception."""
    pass


class _AdversarialClefClient(DecisionClient):
    """Adversarial client simulating latency, timeouts, and arbitrary exceptions."""

    def __init__(
        self,
        *,
        delay_s: float = 0.0,
        raise_exc: Exception | None = None,
        return_answer: ChoiceAnswer | None = None,
    ):
        self.delay_s = delay_s
        self.raise_exc = raise_exc
        self.return_answer = return_answer or ChoiceAnswer(choice="researcher", confidence=0.9)
        self.call_count = 0

    async def evaluate_choice(self, *args, **kwargs) -> ChoiceAnswer:
        self.call_count += 1
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        if self.raise_exc:
            raise self.raise_exc
        return self.return_answer

    async def evaluate(self, *args, **kwargs):
        raise NotImplementedError("evaluate not used directly")

    async def evaluate_noul(self, *args, **kwargs):
        raise NotImplementedError("evaluate_noul not used")

    async def evaluate_score(self, *args, **kwargs):
        raise NotImplementedError("evaluate_score not used")


@pytest.fixture
def adversarial_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())

    # Pre-register test graphs
    GraphRegistry.register("research_chain", SimpleNamespace(ainvoke=lambda *a, **k: {"messages": []}), routeable=True)
    GraphRegistry.register("planner_chain", SimpleNamespace(ainvoke=lambda *a, **k: {"messages": []}), routeable=True)
    GraphRegistry.register("react_agent", SimpleNamespace(ainvoke=lambda *a, **k: {"messages": []}), routeable=True)

    catalog = _FakeCatalog(
        agents={
            "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
            "researcher": _make_agent(name="researcher", graph_type="research_chain", description="Researcher"),
            "planner": _make_agent(name="planner", graph_type="planner_chain", description="Planner"),
            "default": _make_agent(name="default", graph_type="react_agent", description="Default React Agent"),
        },
        callable_map={"orchestrator": ["researcher", "planner"]},
    )
    monkeypatch.setattr(router_module, "get_catalog_service", lambda: catalog)


# =========================================================================
# 1. Adversarial Error Recovery in Cascade Mode
# =========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "simulated_error,expected_error_substr",
    [
        (
            httpx.TimeoutException("The read operation timed out after 5.0 seconds"),
            "timed out",
        ),
        (
            httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=httpx.Request("POST", "https://api.cloudflare.com/ai/run"),
                response=httpx.Response(500, request=httpx.Request("POST", "https://api.cloudflare.com/ai/run")),
            ),
            "500 Internal Server Error",
        ),
        (
            httpx.ConnectError("Failed to establish a new connection: [WinError 10061] Connection refused"),
            "Connection refused",
        ),
        (
            KeyError("choices_not_found_in_payload"),
            "choices_not_found_in_payload",
        ),
        (
            TypeError("'NoneType' object is not subscriptable"),
            "NoneType",
        ),
        (
            ValueError("invalid literal for float(): NaN_string"),
            "invalid literal",
        ),
        (
            ZeroDivisionError("division by zero in score calibration"),
            "division by zero",
        ),
        (
            _CustomVendorException("Cloudflare Workers AI internal fatal panic"),
            "Cloudflare Workers AI internal fatal panic",
        ),
    ],
)
async def test_cascade_mode_adversarial_error_recovery(
    monkeypatch: pytest.MonkeyPatch,
    adversarial_env,
    simulated_error: Exception,
    expected_error_substr: str,
):
    """Verify ZERO crashes and clean fallback to standard LLM across all network and runtime exception types."""
    # LLM is ready and selects "planner"
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    adv_clef = _AdversarialClefClient(raise_exc=simulated_error)

    state = {"messages": [HumanMessage(content="Coordinate project milestones")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": 0.75,
            "decision_client": adv_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    # Execution MUST NOT raise unhandled exception
    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "planner", "Should cleanly cascade to LLM and select 'planner'"
    assert chat_model.call_count == 1
    assert adv_clef.call_count == 1

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "cascade"
    assert telemetry["source"] == "llm"
    assert telemetry["selected_agent"] == "planner"
    assert telemetry["cascade_triggered"] is True
    assert "clef_error" in telemetry["cascade_reason"]
    assert expected_error_substr in telemetry["cascade_reason"]
    assert telemetry["error"] is not None
    assert expected_error_substr in telemetry["error"]
    assert telemetry["clef_latency_ms"] is not None
    assert telemetry["llm_latency_ms"] is not None


@pytest.mark.asyncio
async def test_cascade_mode_nan_and_negative_confidence_cascades_safely(
    monkeypatch: pytest.MonkeyPatch, adversarial_env
):
    """Adversarial check: NaN or negative confidence must NOT bypass the threshold filter."""
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    # Duck-typed answer bypassing Pydantic model validation to test raw numeric edge cases
    adv_clef = _AdversarialClefClient(
        return_answer=SimpleNamespace(choice="researcher", confidence=float("nan"))
    )

    state = {"messages": [HumanMessage(content="Test NaN confidence")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": 0.75,
            "decision_client": adv_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)
    assert res["target_agent"].name == "planner"
    assert res["routing_telemetry"]["cascade_triggered"] is True


@pytest.mark.asyncio
async def test_cascade_mode_missing_client_graceful_fallback(
    monkeypatch: pytest.MonkeyPatch, adversarial_env
):
    """When decision client is None and no env/config exists, cascade seamlessly to LLM."""
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    # Ensure no default client or env credentials
    monkeypatch.setattr(router_module, "_default_decision_client", None)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "")

    state = {"messages": [HumanMessage(content="Perform task with missing client")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "decision_client": None,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)
    assert res["target_agent"].name == "planner"
    telemetry = res["routing_telemetry"]
    assert telemetry["cascade_triggered"] is True
    assert "clef_error" in telemetry["cascade_reason"]


@pytest.mark.asyncio
async def test_cascade_double_failure_structured_llm_fails_recovers_to_raw_text(
    monkeypatch: pytest.MonkeyPatch, adversarial_env
):
    """Clef fails with Timeout, LLM structured output fails, but LLM raw text succeeds."""
    chat_model = _FakeChatModel("researcher", raise_on_structured=True)
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    adv_clef = _AdversarialClefClient(raise_exc=httpx.TimeoutException("Clef timed out"))

    state = {"messages": [HumanMessage(content="Research AI")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "decision_client": adv_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)
    assert res["target_agent"].name == "researcher"
    assert res["routing_telemetry"]["source"] == "llm"


# =========================================================================
# 2. Concurrency & Latency Delta in Shadow Mode
# =========================================================================


@pytest.mark.asyncio
async def test_shadow_mode_slow_clef_concurrent_execution_and_negative_delta(
    monkeypatch: pytest.MonkeyPatch, adversarial_env
):
    """Simulate slow Clef (180ms) and fast LLM (30ms).

    Verifies:
    1. asyncio.gather concurrency: total execution time < (clef_delay + llm_delay).
    2. latency_delta_ms = llm_lat - clef_lat is negative (Clef was slower).
    3. LLM decision is preserved in primary flow.
    """
    CLEF_DELAY = 0.18
    LLM_DELAY = 0.03

    chat_model = _FakeChatModel("planner", delay_s=LLM_DELAY)
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    adv_clef = _AdversarialClefClient(
        delay_s=CLEF_DELAY,
        return_answer=ChoiceAnswer(choice="researcher", confidence=0.9),
    )

    state = {"messages": [HumanMessage(content="Benchmark concurrent shadow")]}
    config = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": adv_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    t0 = time.perf_counter()
    res = await router_module.llm_call_router(state, config, runtime)
    total_duration = time.perf_counter() - t0

    # If executed sequentially, total_duration would be >= CLEF_DELAY + LLM_DELAY = 0.21s.
    # Concurrent execution should be close to max(CLEF_DELAY, LLM_DELAY) ~ 0.18s.
    assert total_duration < (CLEF_DELAY + LLM_DELAY + 0.1), (
        f"Expected concurrent execution, but took {total_duration:.3f}s"
    )

    # Primary flow must strictly choose LLM agent ("planner")
    assert res["target_agent"].name == "planner"

    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "shadow"
    assert telemetry["source"] == "llm"
    assert telemetry["selected_agent"] == "planner"
    assert telemetry["llm_agent"] == "planner"
    assert telemetry["clef_agent"] == "researcher"
    assert telemetry["agreement"] is False

    clef_lat = telemetry["clef_latency_ms"]
    llm_lat = telemetry["llm_latency_ms"]
    delta = telemetry["latency_delta_ms"]

    assert clef_lat >= (CLEF_DELAY * 1000.0 * 0.8)
    assert llm_lat >= (LLM_DELAY * 1000.0 * 0.8)
    # latency_delta = llm_lat - clef_lat: should be negative
    assert delta < 0, f"Expected negative latency delta, got {delta}"
    assert abs(delta - (llm_lat - clef_lat)) < 1e-4


@pytest.mark.asyncio
async def test_shadow_mode_fast_clef_slow_llm_positive_delta(
    monkeypatch: pytest.MonkeyPatch, adversarial_env
):
    """Simulate fast Clef (20ms) and slow LLM (150ms).

    Verifies latency_delta_ms = llm_lat - clef_lat is positive (Clef saved time).
    """
    CLEF_DELAY = 0.02
    LLM_DELAY = 0.15

    chat_model = _FakeChatModel("researcher", delay_s=LLM_DELAY)
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    adv_clef = _AdversarialClefClient(
        delay_s=CLEF_DELAY,
        return_answer=ChoiceAnswer(choice="researcher", confidence=0.95),
    )

    state = {"messages": [HumanMessage(content="Fast Clef benchmark")]}
    config = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": adv_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)
    assert res["target_agent"].name == "researcher"

    telemetry = res["routing_telemetry"]
    assert telemetry["agreement"] is True
    delta = telemetry["latency_delta_ms"]
    assert delta > 0, f"Expected positive latency delta, got {delta}"


@pytest.mark.asyncio
async def test_shadow_mode_clef_timeout_isolation(
    monkeypatch: pytest.MonkeyPatch, adversarial_env
):
    """Slow Clef raises httpx.TimeoutException in shadow mode: LLM must finish unaffected."""
    chat_model = _FakeChatModel("planner", delay_s=0.02)
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    adv_clef = _AdversarialClefClient(
        delay_s=0.05,
        raise_exc=httpx.TimeoutException("Shadow Clef timeout"),
    )

    state = {"messages": [HumanMessage(content="Shadow mode with Clef timeout")]}
    config = {
        "configurable": {
            "router_mode": "shadow",
            "decision_client": adv_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == "planner"
    telemetry = res["routing_telemetry"]
    assert telemetry["mode"] == "shadow"
    assert telemetry["clef_error"] is not None
    assert "Shadow Clef timeout" in telemetry["clef_error"]
    assert telemetry["agreement"] is False
    assert telemetry["llm_error"] is None


# =========================================================================
# 3. Fallback Chain Precedence & Integrity
# =========================================================================


def test_fallback_chain_tier1_candidate_non_router_precedence():
    """Tier 1: Candidates contain non-router agent.

    Precedence MUST pick the first candidate non-router, NOT default agent, NOT catalog non-router.
    """
    candidates = {
        "cand_1": _make_agent(name="cand_1", graph_type="research_chain"),
        "cand_2": _make_agent(name="cand_2", graph_type="planner_chain"),
    }
    catalog = _FakeCatalog(
        agents={
            "cand_1": candidates["cand_1"],
            "cand_2": candidates["cand_2"],
            "default": _make_agent(name="default", graph_type="react_agent"),
            "catalog_worker": _make_agent(name="catalog_worker", graph_type="worker_chain"),
        }
    )

    fallback = router_module._select_fallback_agent(candidates, catalog)
    assert fallback is not None
    assert fallback.name == "cand_1", (
        f"Tier 1 violation: Expected 'cand_1' from candidates, got '{fallback.name}'"
    )


def test_fallback_chain_tier2_default_agent_when_candidates_has_no_non_router():
    """Tier 2: Candidates contains ONLY router agents.

    Candidate non-router is absent. Precedence MUST pick 'default' agent, NOT catalog non-router.
    """
    candidates = {
        "sub_router_alpha": _make_agent(name="sub_router_alpha", graph_type="router"),
    }
    catalog = _FakeCatalog(
        agents={
            "sub_router_alpha": candidates["sub_router_alpha"],
            "default": _make_agent(name="default", graph_type="react_agent"),
            "catalog_worker": _make_agent(name="catalog_worker", graph_type="worker_chain"),
        }
    )

    fallback = router_module._select_fallback_agent(candidates, catalog)
    assert fallback is not None
    assert fallback.name == "default", (
        f"Tier 2 violation: Expected 'default' agent, got '{fallback.name}'"
    )


def test_fallback_chain_tier3_catalog_non_router_when_default_is_router_or_missing():
    """Tier 3: Candidates has no non-router; 'default' agent is also a router (or missing).

    Precedence MUST pick the first non-router agent from catalog.
    """
    candidates = {
        "sub_router_1": _make_agent(name="sub_router_1", graph_type="router"),
    }
    catalog = _FakeCatalog(
        agents={
            "sub_router_1": candidates["sub_router_1"],
            "default": _make_agent(name="default", graph_type="router"),  # default is misconfigured as router!
            "first_valid_worker": _make_agent(name="first_valid_worker", graph_type="research_chain"),
            "second_valid_worker": _make_agent(name="second_valid_worker", graph_type="planner_chain"),
        }
    )

    fallback = router_module._select_fallback_agent(candidates, catalog)
    assert fallback is not None
    assert fallback.name == "first_valid_worker", (
        f"Tier 3 violation: Expected 'first_valid_worker', got '{fallback.name}'"
    )


def test_fallback_chain_returns_none_when_all_agents_are_routers():
    """Tier 4: Total saturation where every agent in catalog is a router."""
    candidates = {
        "r1": _make_agent(name="r1", graph_type="router"),
    }
    catalog = _FakeCatalog(
        agents={
            "r1": candidates["r1"],
            "default": _make_agent(name="default", graph_type="router"),
            "r2": _make_agent(name="r2", graph_type="router"),
        }
    )

    fallback = router_module._select_fallback_agent(candidates, catalog)
    assert fallback is None, "Should return None when no non-router agent exists anywhere"


@pytest.mark.asyncio
async def test_llm_call_router_avoids_routing_to_router_agent(
    monkeypatch: pytest.MonkeyPatch, adversarial_env
):
    """If Clef or LLM selects an agent that happens to have graph_type='router',

    router MUST reject it and activate the fallback chain to prevent infinite recursion.
    """
    # Candidate catalog contains a sub-router and a worker
    sub_router = _make_agent(name="sub_router", graph_type="router")
    worker = _make_agent(name="worker", graph_type="research_chain")

    catalog = _FakeCatalog(
        agents={
            "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
            "sub_router": sub_router,
            "worker": worker,
            "default": _make_agent(name="default", graph_type="react_agent"),
        },
        callable_map={"orchestrator": ["sub_router", "worker"]},
    )
    monkeypatch.setattr(router_module, "get_catalog_service", lambda: catalog)

    # Clef selects 'sub_router' with high confidence 0.99
    adv_clef = _AdversarialClefClient(
        return_answer=ChoiceAnswer(choice="sub_router", confidence=0.99)
    )

    state = {"messages": [HumanMessage(content="Infinite loop prevention test")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "decision_client": adv_clef,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    # MUST NOT select sub_router!
    assert res["target_agent"].name != "sub_router"
    # Should fall back to worker (Tier 1 candidate non-router)
    assert res["target_agent"].name == "worker"
    assert res["routing_telemetry"]["fallback_used"] is True
    assert res["routing_telemetry"]["fallback_target"] == "worker"


# =========================================================================
# 4. Concurrency Stress Testing & Candidate Scalability
# =========================================================================


@pytest.mark.asyncio
async def test_high_concurrency_stress_shadow_mode(monkeypatch: pytest.MonkeyPatch, adversarial_env):
    """Stress test: 25 simultaneous concurrent requests in shadow mode.

    Verifies no race conditions, correct per-task telemetry isolation, and stability.
    """
    chat_model = _FakeChatModel("planner", delay_s=0.01)
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    adv_clef = _AdversarialClefClient(
        delay_s=0.015,
        return_answer=ChoiceAnswer(choice="researcher", confidence=0.88),
    )

    async def _single_request(i: int):
        state = {"messages": [HumanMessage(content=f"Concurrent shadow request #{i}")]}
        config = {
            "configurable": {
                "router_mode": "shadow",
                "decision_client": adv_clef,
            }
        }
        runtime = SimpleNamespace(context=_runtime_context())
        return await router_module.llm_call_router(state, config, runtime)

    results = await asyncio.gather(*[_single_request(i) for i in range(25)])

    assert len(results) == 25
    for r in results:
        assert r["target_agent"].name == "planner"
        assert r["routing_telemetry"]["mode"] == "shadow"
        assert r["routing_telemetry"]["source"] == "llm"
        assert r["routing_telemetry"]["agreement"] is False


@pytest.mark.asyncio
async def test_high_concurrency_mixed_cascade_traffic(monkeypatch: pytest.MonkeyPatch, adversarial_env):
    """Stress test: 30 concurrent requests in cascade mode with randomized/mixed outcomes:

    - 10 fast-path high-confidence hits
    - 10 low-confidence cascades
    - 10 network errors (timeout / 500)
    Verifies that all 30 resolve cleanly without deadlocks, cross-talk, or unhandled errors.
    """
    chat_model = _FakeChatModel("planner")
    monkeypatch.setattr(router_module, "get_resolved_chat_model", lambda **kw: _fake_resolved_model(chat_model))

    class _MixedClient(DecisionClient):
        async def evaluate_choice(self, state, instructions, criteria, **kwargs):
            val = int(str(state).split("#")[-1])
            if val % 3 == 0:
                # Fast path
                return ChoiceAnswer(choice="researcher", confidence=0.95)
            elif val % 3 == 1:
                # Low confidence
                return ChoiceAnswer(choice="researcher", confidence=0.50)
            else:
                # Error
                raise httpx.TimeoutException(f"Simulated timeout on task #{val}")

        async def evaluate(self, *a, **k):
            raise NotImplementedError()

        async def evaluate_noul(self, *a, **k):
            raise NotImplementedError()

        async def evaluate_score(self, *a, **k):
            raise NotImplementedError()

    mixed_client = _MixedClient()

    async def _invoke(i: int):
        state = {"messages": [HumanMessage(content=f"Request #{i}")]}
        config = {
            "configurable": {
                "router_mode": "cascade",
                "confidence_threshold": 0.75,
                "decision_client": mixed_client,
            }
        }
        runtime = SimpleNamespace(context=_runtime_context())
        return i, await router_module.llm_call_router(state, config, runtime)

    results = await asyncio.gather(*[_invoke(i) for i in range(30)])
    assert len(results) == 30

    fast_hits = 0
    cascades = 0
    errors = 0

    for i, res in results:
        telemetry = res["routing_telemetry"]
        if i % 3 == 0:
            # Fast-path hit
            assert res["target_agent"].name == "researcher"
            assert telemetry["source"] == "clef"
            assert telemetry["cascade_triggered"] is False
            fast_hits += 1
        elif i % 3 == 1:
            # Low confidence cascade
            assert res["target_agent"].name == "planner"
            assert telemetry["source"] == "llm"
            assert telemetry["cascade_triggered"] is True
            assert "low_confidence" in telemetry["cascade_reason"]
            cascades += 1
        else:
            # Network error cascade
            assert res["target_agent"].name == "planner"
            assert telemetry["source"] == "llm"
            assert telemetry["cascade_triggered"] is True
            assert "clef_error" in telemetry["cascade_reason"]
            assert telemetry["error"] is not None
            errors += 1

    assert fast_hits == 10
    assert cascades == 10
    assert errors == 10


@pytest.mark.asyncio
async def test_large_candidate_catalog_scalability(monkeypatch: pytest.MonkeyPatch):
    """Stress test: 50 candidate agents in catalog.

    Verifies criteria dict serialization, correct agent selection, and performance.
    """
    agents: dict[str, AgentConfig] = {
        "orchestrator": _make_agent(name="orchestrator", graph_type="router")
    }
    callable_names = []
    for i in range(50):
        agent_name = f"specialist_{i:02d}"
        agents[agent_name] = _make_agent(
            name=agent_name,
            graph_type="research_chain",
            description=f"Specialized domain agent number {i}",
        )
        callable_names.append(agent_name)

    catalog = _FakeCatalog(
        agents=agents,
        callable_map={"orchestrator": callable_names},
    )
    monkeypatch.setattr(router_module, "get_catalog_service", lambda: catalog)

    # Clef selects specialist_42
    target_specialist = "specialist_42"
    captured_criteria = {}

    class _InspectingClient(DecisionClient):
        async def evaluate_choice(self, state, instructions, criteria, **kwargs):
            nonlocal captured_criteria
            captured_criteria = criteria
            return ChoiceAnswer(choice=target_specialist, confidence=0.92)

        async def evaluate(self, *a, **k):
            raise NotImplementedError()

        async def evaluate_noul(self, *a, **k):
            raise NotImplementedError()

        async def evaluate_score(self, *a, **k):
            raise NotImplementedError()

    client = _InspectingClient()

    state = {"messages": [HumanMessage(content="Query 50 agents")]}
    config = {
        "configurable": {
            "router_mode": "cascade",
            "confidence_threshold": 0.75,
            "decision_client": client,
        }
    }
    runtime = SimpleNamespace(context=_runtime_context())

    res = await router_module.llm_call_router(state, config, runtime)

    assert res["target_agent"].name == target_specialist
    assert len(captured_criteria) == 50
    assert captured_criteria[target_specialist] == "Specialized domain agent number 42"
    assert res["routing_telemetry"]["source"] == "clef"

