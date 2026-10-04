"""Comprehensive test suite for DecisionService, settings, evaluators, and container integration."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from agent.bootstrap.container import AppContainer
from agent.modules.decisions import (
    AgentRoutingEvaluator,
    BaseDecisionEvaluator,
    ChannelInboundTriageEvaluator,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionClientError,
    DecisionResult,
    DecisionService,
    DecisionSettings,
    MockDecisionClient,
    NoulAnswer,
    NoulQuestion,
    ResearchStoppingContext,
    ResearchStoppingDecision,
    ResearchStoppingEvaluator,
    RouterMode,
    RoutingContext,
    RoutingDecision,
    SafetyCheckContext,
    SafetyCheckDecision,
    SafetyCheckEvaluator,
    ScoreAnswer,
    ScoreQuestion,
    ToolFilterContext,
    ToolFilterDecision,
    ToolPreFilterEvaluator,
    TriageContext,
    TriageDecision,
    load_decision_settings,
)


# --- Settings Tests ---


def test_decision_settings_defaults():
    settings = DecisionSettings()
    assert settings.cloudflare_account_id == ""
    assert settings.cloudflare_api_token == ""
    assert settings.model == "@cf/cloudflare/clef-flash"
    assert settings.timeout == 5.0
    assert settings.max_retries == 2
    assert settings.router_mode == RouterMode.CASCADE
    assert settings.router_threshold == 0.75
    assert settings.router_log_telemetry is True


def test_load_decision_settings_env_fallback(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "env_acc_123")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "env_token_456")

    mock_config = MagicMock()
    mock_config.get_str.return_value = ""
    mock_config.get.return_value = None
    mock_config.get_int.return_value = 2
    mock_config.get_bool.return_value = True

    settings = load_decision_settings(mock_config)
    assert settings.cloudflare_account_id == "env_acc_123"
    assert settings.cloudflare_api_token == "env_token_456"
    assert settings.router_mode == RouterMode.CASCADE


# --- DecisionService Tests ---


@pytest.mark.asyncio
async def test_decision_service_with_mock_client():
    mock_client = MockDecisionClient()
    mock_client.set_canned_answer(
        "q1",
        ChoiceAnswer(
            choice="coder",
            confidence=0.92,
            probabilities={"coder": 0.92, "general": 0.08},
        ),
    )

    service = DecisionService(client=mock_client)
    assert service.is_configured is True
    assert service.total_evaluations == 0

    result = await service.evaluate(
        "Fix the authentication bug",
        {"q1": ChoiceQuestion(instructions="Pick agent", criteria={"coder": "Code", "general": "Chat"})},
    )

    assert isinstance(result, DecisionResult)
    assert result.answers["q1"].choice == "coder"
    assert service.total_evaluations == 1
    assert service.total_latency_ms > 0
    assert service.average_latency_ms > 0

    metrics = service.get_metrics()
    assert metrics["total_evaluations"] == 1
    assert metrics["total_errors"] == 0

    service.reset_metrics()
    assert service.total_evaluations == 0


@pytest.mark.asyncio
async def test_decision_service_convenience_methods():
    mock_client = MockDecisionClient()
    mock_client.set_canned_answer(
        "*",
        ChoiceAnswer(choice="opt_a", confidence=0.85, probabilities={"opt_a": 0.85}),
    )

    service = DecisionService(client=mock_client)
    choice_ans = await service.evaluate_choice(
        state="Sample input",
        instructions="Choose option",
        criteria={"opt_a": "First", "opt_b": "Second"},
    )
    assert isinstance(choice_ans, ChoiceAnswer)
    assert choice_ans.choice == "opt_a"
    assert choice_ans.confidence == 0.85

    mock_client.set_canned_answer("*", NoulAnswer(noul=0.88, decision=True, confidence=0.88))
    noul_ans = await service.evaluate_noul(
        state="Is this urgent?",
        instructions="Urgent check",
    )
    assert isinstance(noul_ans, NoulAnswer)
    assert noul_ans.noul == 0.88
    assert noul_ans.decision is True

    mock_client.set_canned_answer(
        "*",
        ScoreAnswer(score=2.5, confidence=0.8, probabilities={"0": 0.1, "1": 0.1, "2": 0.5, "3": 0.3}),
    )
    score_ans = await service.evaluate_score(
        state="Rate risk",
        instructions="Risk level",
        criteria=["low", "medium", "high", "critical"],
    )
    assert isinstance(score_ans, ScoreAnswer)
    assert score_ans.score == 2.5


@pytest.mark.asyncio
async def test_decision_service_unconfigured_raises():
    service = DecisionService(client=None, settings=DecisionSettings())
    assert service.is_configured is False

    with pytest.raises(DecisionClientError, match="DecisionClient is not configured"):
        await service.evaluate(
            "test",
            {"q1": NoulQuestion(instructions="Yes/No")},
        )


# --- Domain Evaluators Tests ---


@pytest.mark.asyncio
async def test_agent_routing_evaluator():
    mock_client = MockDecisionClient()
    mock_client.set_canned_answer(
        "*",
        ChoiceAnswer(
            choice="github_agent",
            confidence=0.88,
            probabilities={"github_agent": 0.88, "channel_agent": 0.12},
        ),
    )

    service = DecisionService(client=mock_client)
    evaluator = AgentRoutingEvaluator(service=service, confidence_threshold=0.75)
    context = RoutingContext(
        user_input="Review pull request #42",
        candidates={"github_agent": "PR review", "channel_agent": "General chat"},
        caller_agent_name="user",
    )

    decision = await evaluator.evaluate(context)
    assert isinstance(decision, RoutingDecision)
    assert decision.selected_agent == "github_agent"
    assert decision.confidence == 0.88
    assert decision.meets_threshold is True


@pytest.mark.asyncio
async def test_channel_inbound_triage_evaluator():
    mock_client = MockDecisionClient()
    mock_client.set_canned_answer(
        "*",
        ChoiceAnswer(
            choice="respond_code",
            confidence=0.91,
            probabilities={"respond_code": 0.91, "respond_default": 0.09},
        ),
    )

    service = DecisionService(client=mock_client)
    evaluator = ChannelInboundTriageEvaluator(service=service)
    context = TriageContext(
        message_text="Please run the pytest suite on my branch",
        sender_id="user_123",
        channel="telegram",
    )

    decision = await evaluator.evaluate(context)
    assert isinstance(decision, TriageDecision)
    assert decision.action == "respond_code"
    assert decision.confidence == 0.91
    assert decision.is_spam is False


@pytest.mark.asyncio
async def test_tool_pre_filter_evaluator():
    mock_client = MockDecisionClient()
    mock_client.set_canned_answer(
        "*",
        ChoiceAnswer(choice="filesystem", confidence=0.89, probabilities={"filesystem": 0.89}),
    )

    service = DecisionService(client=mock_client)
    evaluator = ToolPreFilterEvaluator(service=service)
    context = ToolFilterContext(
        user_query="Read content of config.yaml file",
        available_tools=["read", "write", "web_search", "run_bash"],
    )

    decision = await evaluator.evaluate(context)
    assert isinstance(decision, ToolFilterDecision)
    assert decision.selected_category == "filesystem"
    assert "filesystem" in decision.relevant_categories


@pytest.mark.asyncio
async def test_research_stopping_evaluator():
    mock_client = MockDecisionClient()
    mock_client.set_canned_answer(
        "*",
        NoulAnswer(noul=0.85, decision=True, confidence=0.85),
    )

    service = DecisionService(client=mock_client)
    evaluator = ResearchStoppingEvaluator(service=service, confidence_threshold=0.75)
    context = ResearchStoppingContext(
        query="Impact of asyncio in Python 3.13",
        notes=["Comprehensive benchmark showing 15% throughput increase across all tasks."],
        iteration=3,
        max_iterations=5,
    )

    decision = await evaluator.evaluate(context)
    assert isinstance(decision, ResearchStoppingDecision)
    assert decision.should_stop is True
    assert decision.sufficiency_prob == 0.85


@pytest.mark.asyncio
async def test_safety_check_evaluator():
    mock_client = MockDecisionClient()
    mock_client.set_canned_answer(
        "*",
        NoulAnswer(noul=0.95, decision=True, confidence=0.95),
    )

    service = DecisionService(client=mock_client)
    evaluator = SafetyCheckEvaluator(service=service, confidence_threshold=0.75)
    context = SafetyCheckContext(
        prompt="rm -rf /var/data/cache",
    )

    decision = await evaluator.evaluate(context)
    assert isinstance(decision, SafetyCheckDecision)
    assert decision.violation_detected is True
    assert decision.is_safe is False
    assert decision.risk_probability == 0.95


# --- AppContainer Integration Test ---


def test_app_container_decision_service_property():
    container = AppContainer()
    service = container.decision_service
    assert isinstance(service, DecisionService)
    # Lazy property must return identical cached instance
    assert container.decision_service is service
