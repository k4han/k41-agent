"""Google Decisions protocol, validation, retries, and resource ownership."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from agent.modules.decisions import (
    BaseQuestion, ChoiceAnswer, ChoiceQuestion, DecisionAPIError,
    DecisionAuthenticationError, DecisionClientError, DecisionRateLimitError,
    DecisionTimeoutError, GoogleDecisionClient, NoulAnswer, NoulQuestion,
    ScoreAnswer, ScoreQuestion,
)


@pytest_asyncio.fixture
async def google_client_factory():
    clients = []

    def create(handler, **kwargs):
        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(transport)
        return GoogleDecisionClient("private-key", client=transport, **{"max_retries": 0, **kwargs})

    yield create
    for client in clients:
        await client.aclose()


def noul_response():
    return {"answers": {"noul_q": {"type": "noul", "noul": 0.7, "confidence": 0.7}}}


@pytest.mark.asyncio
async def test_mixed_questions_json_context_and_local_threshold(google_client_factory):
    raw = {
        "answers": {
            "binary": {"type": "noul", "noul": 0.7, "confidence": 0.7, "decision": True},
            "route": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.9, "other": 0.1}, "confidence": 0.9},
            "priority": {"type": "score", "score": 1.4, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
                         "legend": {"0": "Low", "1": "Medium", "2": "High"}, "confidence": 0.5},
        },
        "usage": {"total_input_tokens": 123, "total_output_tokens": 0},
    }

    def handler(request):
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/decisions"
        assert request.headers["x-goog-api-key"] == "private-key"
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        assert payload["model"] == "custom-decision-model"
        assert json.loads(payload["input"]) == {"message": "Duplicate charge", "amount": 10}
        assert "state" not in payload
        assert payload["questions"]["binary"] == {"type": "noul", "instructions": "Is this a payment issue?"}
        assert payload["questions"]["priority"]["criteria"] == ["Low", "Medium", "High"]
        return httpx.Response(200, json=raw)

    client = google_client_factory(handler)
    result = await client.evaluate({"message": "Duplicate charge", "amount": 10}, {
        "binary": NoulQuestion(instructions="Is this a payment issue?", threshold=0.8),
        "route": ChoiceQuestion(instructions="Choose department", criteria={"billing": "Payment problems", "other": "Other issues"}),
        "priority": ScoreQuestion(instructions="Rate urgency", criteria=["Low", "Medium", "High"]),
    }, model="custom-decision-model")
    assert result.model == "custom-decision-model"
    assert isinstance(result.answers["binary"], NoulAnswer)
    assert result.answers["binary"].decision is False
    assert result.answers["binary"].confidence == 0.7
    assert isinstance(result.answers["route"], ChoiceAnswer)
    assert result.answers["route"].choice == "billing"
    assert isinstance(result.answers["priority"], ScoreAnswer)
    assert result.answers["priority"].selected_level == "2"
    assert result.raw_response == raw
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_text_input_and_custom_base_url(google_client_factory):
    def handler(request):
        assert str(request.url) == "https://proxy.example/api/v1beta/decisions"
        assert json.loads(request.content)["input"] == "Context as text"
        return httpx.Response(200, json=noul_response())

    client = google_client_factory(handler, base_url="https://proxy.example/api/v1beta/")
    answer = await client.evaluate_noul("Context as text", "Is this context?", threshold=0.7)
    assert answer.decision is True


@pytest.mark.parametrize("questions", [
    {}, {str(i): NoulQuestion(instructions="Check") for i in range(1001)},
    {"bad key": NoulQuestion(instructions="Check")}, {"x" * 65: NoulQuestion(instructions="Check")},
    {"q": BaseQuestion(instructions="Unsupported")}, {"q": NoulQuestion(instructions=" ")},
    {"q": ChoiceQuestion(instructions="Choose", criteria={"only": "One"})},
    {"q": ChoiceQuestion(instructions="Choose", criteria={str(i): "" for i in range(256)})},
    {"q": ChoiceQuestion(instructions="Choose", criteria={" invalid": "", "valid": ""})},
    {"q": ChoiceQuestion(instructions="Choose", criteria={"a\u00a0b": "", "valid": ""})},
    {"q": ChoiceQuestion(instructions="Choose", criteria={"x" * 129: "", "valid": ""})},
    {"q": ScoreQuestion(instructions="Rate", criteria=["One"])},
    {"q": ScoreQuestion(instructions="Rate", criteria=["One", " "])},
    {"q": ScoreQuestion(instructions="Rate", criteria=["Level"] * 256)},
])
@pytest.mark.asyncio
async def test_invalid_questions_rejected_before_network(google_client_factory, questions):
    def handler(request):
        pytest.fail("Invalid questions must not reach the network")

    with pytest.raises(DecisionClientError):
        await google_client_factory(handler).evaluate("Context", questions)


@pytest.mark.parametrize("state", [[{"type": "image", "uri": "https://example.com/image.png"}], {"value": float("nan")}, {"value": object()}])
@pytest.mark.asyncio
async def test_invalid_input_rejected_before_network(google_client_factory, state):
    def handler(request):
        pytest.fail("Invalid input must not reach the network")

    with pytest.raises(DecisionClientError):
        await google_client_factory(handler).evaluate(state, {"q": NoulQuestion(instructions="Check")})


@pytest.mark.parametrize("body", [
    [], {}, {"answers": []}, {"answers": {}},
    {"answers": {"q": {"type": "choice", "noul": 0.7, "confidence": 0.7}}},
    {"answers": {"q": {"type": "noul", "noul": 0.7}}},
    {"answers": {"q": {"type": "noul", "noul": 1.1, "confidence": 0.7}}},
])
@pytest.mark.asyncio
async def test_malformed_success_raises_api_error(google_client_factory, body):
    client = google_client_factory(lambda request: httpx.Response(200, json=body))
    with pytest.raises(DecisionAPIError, match="Malformed response"):
        await client.evaluate("Context", {"q": NoulQuestion(instructions="Check")})


@pytest.mark.parametrize("answer,question", [
    ({"type": "choice", "choice": "unknown", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.9},
     ChoiceQuestion(instructions="Choose", criteria={"a": "A", "b": "B"})),
    ({"type": "choice", "choice": "a", "probabilities": {"a": 0.9}, "confidence": 0.9},
     ChoiceQuestion(instructions="Choose", criteria={"a": "A", "b": "B"})),
    ({"type": "score", "score": "nan", "probabilities": {"0": 0.9, "1": 0.1}, "confidence": 0.9},
     ScoreQuestion(instructions="Rate", criteria=["Low", "High"])),
    ({"type": "score", "score": 2, "probabilities": {"0": 0.9, "1": 0.1}, "confidence": 0.9},
     ScoreQuestion(instructions="Rate", criteria=["Low", "High"])),
])
@pytest.mark.asyncio
async def test_invalid_typed_answers_rejected(google_client_factory, answer, question):
    client = google_client_factory(lambda request: httpx.Response(200, json={"answers": {"q": answer}}))
    with pytest.raises(DecisionAPIError):
        await client.evaluate("Context", {"q": question})


@pytest.mark.parametrize("status,body,error_type", [
    (401, {}, DecisionAuthenticationError), (403, {}, DecisionAuthenticationError),
    (400, {"error": {"message": "Invalid private-key", "details": [{"reason": "API_KEY_INVALID"}]}}, DecisionAuthenticationError),
    (400, {"error": {"code": "safety", "message": "Content blocked"}}, DecisionAPIError),
    (400, {"error": {"message": "Invalid input"}}, DecisionAPIError),
    (429, {}, DecisionRateLimitError), (503, {}, DecisionAPIError),
])
@pytest.mark.asyncio
async def test_remote_errors_are_mapped_and_secrets_redacted(google_client_factory, status, body, error_type):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    with pytest.raises(error_type) as caught:
        await google_client_factory(handler).evaluate_noul("Context", "Check")
    assert caught.value.status_code == status
    assert "private-key" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403])
@pytest.mark.asyncio
async def test_permanent_errors_are_not_retried(google_client_factory, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "Rejected"}})

    with pytest.raises(DecisionAPIError):
        await google_client_factory(handler, max_retries=2).evaluate_noul("Context", "Check")
    assert len(calls) == 1


@pytest.mark.parametrize("status,retry_after", [
    (429, "0.25"), (503, "invalid"),
    (429, format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120))),
    (429, "NaN"), (429, "-1"),
])
@pytest.mark.asyncio
async def test_transient_errors_retry_with_bounded_backoff(google_client_factory, monkeypatch, status, retry_after):
    sleep = AsyncMock()
    monkeypatch.setattr("agent.modules.decisions.google.asyncio.sleep", sleep)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, headers={"Retry-After": retry_after})
        return httpx.Response(200, json=noul_response())

    await google_client_factory(handler, max_retries=1).evaluate_noul("Context", "Check")
    assert len(calls) == 2
    delay = sleep.await_args.args[0]
    assert 0 <= delay <= 5
    if retry_after == "0.25":
        assert delay == 0.25


@pytest.mark.parametrize("exception,error_type", [
    (httpx.ReadTimeout("Timeout"), DecisionTimeoutError),
    (httpx.ConnectError("Connection failed"), DecisionAPIError),
])
@pytest.mark.asyncio
async def test_network_retries_are_exhausted(google_client_factory, monkeypatch, exception, error_type):
    monkeypatch.setattr("agent.modules.decisions.google.asyncio.sleep", AsyncMock())
    calls = []

    def handler(request):
        calls.append(request)
        raise exception

    with pytest.raises(error_type):
        await google_client_factory(handler, max_retries=2).evaluate_noul("Context", "Check")
    assert len(calls) == 3


@pytest.mark.parametrize("status", [200, 403, 429, 502])
@pytest.mark.asyncio
async def test_non_json_responses(google_client_factory, status):
    client = google_client_factory(lambda request: httpx.Response(status, text="<html>private-key</html>"))
    with pytest.raises(DecisionAPIError) as caught:
        await client.evaluate_noul("Context", "Check")
    assert "private-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_http_client_ownership(google_client_factory):
    external = google_client_factory(lambda request: httpx.Response(200, json=noul_response()))
    await external.close()
    await external.evaluate_noul("Context", "Check")
    async with GoogleDecisionClient("private-key") as owned:
        assert not owned._client.is_closed
    assert owned._client.is_closed


@pytest.mark.parametrize("fields", [
    {"api_key": " "}, {"base_url": "file:///tmp"}, {"base_url": "https://user:pass@example.com"},
    {"base_url": "https://example.com?key=secret"}, {"default_model": " "},
    {"timeout": float("nan")}, {"timeout": 0}, {"max_retries": -1}, {"max_retries": 1.5},
])
def test_invalid_constructor_settings(fields):
    with pytest.raises(DecisionClientError):
        GoogleDecisionClient(**{"api_key": "private-key", **fields})
