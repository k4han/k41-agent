"""Comprehensive unit and regression test suite for decision clients and models.

Covers edge-case hardening, resilience, and error handling for:
- CloudflareClefClient (Retry-After header parsing, HTML/non-JSON decoding, scalar unpacking, schema error wrapping)
- MockDecisionClient (canned Exception raising, normalized probability distributions)
- Decision Models (probabilities bounding in [0.0, 1.0], criteria min_length=1)
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any
import httpx
from pydantic import ValidationError
import pytest

from agent.modules.decisions import (
    ChoiceAnswer,
    ChoiceQuestion,
    CloudflareClefClient,
    DecisionAPIError,
    DecisionAuthenticationError,
    DecisionClient,
    DecisionClientError,
    DecisionError,
    DecisionRateLimitError,
    DecisionResult,
    DecisionTimeoutError,
    MockDecisionClient,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)


# --- 1. CloudflareClefClient Retry-After Hardening Tests ---


class TestCloudflareRetryAfter:
    """Verifies safe parsing of numeric, RFC 7231 HTTP-date, and malformed Retry-After headers."""

    @pytest.mark.asyncio
    async def test_numeric_float_retry_after(self):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "0.01"})
            return httpx.Response(
                200,
                json={"success": True, "result": {"answers": {"q": {"choice": "opt_a"}}}},
            )

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=1,
        )
        res = await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"opt_a": "A"})})
        assert call_count == 2
        assert res.answers["q"].choice == "opt_a"

    @pytest.mark.asyncio
    async def test_rfc_7231_http_date_retry_after(self):
        # Far-future date should be parsed safely without raising ValueError
        future_date = "Wed, 21 Oct 2026 07:28:00 GMT"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": future_date})

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=0,  # fail fast so we test exception content
        )
        with pytest.raises(DecisionRateLimitError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"opt_a": "A"})})

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after is not None
        assert exc_info.value.retry_after > 0.0

    @pytest.mark.asyncio
    async def test_past_http_date_retry_after(self):
        past_date = "Sun, 06 Nov 1994 08:49:37 GMT"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": past_date})

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=0,
        )
        with pytest.raises(DecisionRateLimitError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"opt_a": "A"})})

        # Past date max(0.0, dt - now) results in 0.0; if clamped/fallback, remains non-negative
        assert exc_info.value.retry_after is not None
        assert exc_info.value.retry_after >= 0.0

    @pytest.mark.asyncio
    async def test_malformed_string_retry_after_fallback(self):
        for bad_header in ["invalid-timestamp", "", "   ", "NaN", "Infinity", "-10"]:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(429, headers={"Retry-After": bad_header})

            client = CloudflareClefClient(
                account_id="acc",
                api_token="tok",
                client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                max_retries=0,
            )
            with pytest.raises(DecisionRateLimitError) as exc_info:
                await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"opt_a": "A"})})

            assert exc_info.value.status_code == 429
            assert exc_info.value.retry_after is not None
            assert exc_info.value.retry_after > 0.0

    @pytest.mark.asyncio
    async def test_missing_retry_after_header_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=0,
        )
        with pytest.raises(DecisionRateLimitError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"opt_a": "A"})})

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after is not None
        assert exc_info.value.retry_after > 0.0


# --- 2. Safe JSON Decoding on Non-JSON / HTML Responses ---


class TestCloudflareSafeJsonDecoding:
    """Verifies that non-JSON/HTML errors do not crash with JSONDecodeError."""

    @pytest.mark.asyncio
    async def test_html_403_forbidden_raises_authentication_error(self):
        html_body = "<html><body>Cloudflare 403 Forbidden: Access Denied</body></html>"
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, text=html_body))),
            max_retries=0,
        )
        with pytest.raises(DecisionAuthenticationError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.status_code == 403
        assert "403" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_html_401_unauthorized_raises_authentication_error(self):
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="<h1>Unauthorized</h1>"))),
            max_retries=0,
        )
        with pytest.raises(DecisionAuthenticationError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_html_429_rate_limit_raises_rate_limit_error(self):
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, text="<html>Rate Limited</html>"))),
            max_retries=0,
        )
        with pytest.raises(DecisionRateLimitError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_html_502_bad_gateway_raises_api_error(self):
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>Bad Gateway</html>"))),
            max_retries=0,
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.status_code == 502
        assert not isinstance(exc_info.value, DecisionAuthenticationError)

    @pytest.mark.asyncio
    async def test_html_504_gateway_timeout_raises_api_error(self):
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(504, text="<html>Gateway Timeout</html>"))),
            max_retries=0,
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.status_code == 504

    @pytest.mark.asyncio
    async def test_empty_content_500_raises_api_error(self):
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500, content=b""))),
            max_retries=0,
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.status_code == 500


# --- 3. Primitive Scalar Answer Unpacking Tests ---


class TestCloudflarePrimitiveScalarUnpacking:
    """Verifies that CloudflareClefClient unpacks raw scalar responses into typed answer models."""

    @pytest.mark.asyncio
    async def test_noul_question_with_float_scalar(self):
        envelope = {
            "success": True,
            "result": {"answers": {"q_noul": 0.85}},
        }
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope))),
        )
        res = await client.evaluate("state", {"q_noul": NoulQuestion(instructions="is valid?")})
        ans = res.answers["q_noul"]
        assert isinstance(ans, NoulAnswer)
        assert ans.noul == 0.85
        assert ans.decision is True
        assert math.isclose(ans.confidence, 0.85)

    @pytest.mark.asyncio
    async def test_noul_question_with_boolean_scalar(self):
        # Test True
        envelope_true = {
            "success": True,
            "result": {"answers": {"q_bool": True}},
        }
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope_true))),
        )
        res_true = await client.evaluate("state", {"q_bool": NoulQuestion(instructions="check")})
        assert res_true.answers["q_bool"].noul == 1.0
        assert res_true.answers["q_bool"].decision is True

        # Test False
        envelope_false = {
            "success": True,
            "result": {"answers": {"q_bool": False}},
        }
        client_false = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope_false))),
        )
        res_false = await client_false.evaluate("state", {"q_bool": NoulQuestion(instructions="check")})
        assert res_false.answers["q_bool"].noul == 0.0
        assert res_false.answers["q_bool"].decision is False

    @pytest.mark.asyncio
    async def test_score_question_with_float_and_int_scalar(self):
        envelope = {
            "success": True,
            "result": {"answers": {"q_float": 4.5, "q_int": 3}},
        }
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope))),
        )
        res = await client.evaluate(
            "state",
            {
                "q_float": ScoreQuestion(instructions="rate float", criteria=["1", "2", "3", "4", "5"]),
                "q_int": ScoreQuestion(instructions="rate int", criteria=["1", "2", "3", "4", "5"]),
            },
        )
        assert res.answers["q_float"].score == 4.5
        assert res.answers["q_int"].score == 3.0

    @pytest.mark.asyncio
    async def test_choice_question_with_string_scalar(self):
        envelope = {
            "success": True,
            "result": {"answers": {"q_choice": "coder"}},
        }
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope))),
        )
        res = await client.evaluate("state", {"q_choice": ChoiceQuestion(instructions="pick", criteria={"coder": "Code"})})
        ans = res.answers["q_choice"]
        assert isinstance(ans, ChoiceAnswer)
        assert ans.choice == "coder"


# --- 4. Schema Deserialization Error Encapsulation Tests ---


class TestCloudflareSchemaDeserializationWrapping:
    """Verifies that malformed answer shapes from Cloudflare raise DecisionAPIError."""

    @pytest.mark.asyncio
    async def test_missing_required_fields_raises_decision_api_error(self):
        envelope = {
            "success": True,
            "result": {"answers": {"q_c": {"unrelated_field": "val"}}},
        }
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope))),
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate("state", {"q_c": ChoiceQuestion(instructions="pick", criteria={"a": "A"})})

        assert exc_info.value.status_code == 500
        assert "Malformed answer payload" in str(exc_info.value)
        # Verify raw ValidationError did not escape unwrapped
        assert isinstance(exc_info.value, DecisionError)

    @pytest.mark.asyncio
    async def test_missing_question_in_answers_raises_decision_api_error(self):
        envelope = {
            "success": True,
            "result": {"answers": {"different_q": "coder"}},
        }
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope))),
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate("state", {"expected_q": ChoiceQuestion(instructions="pick", criteria={"a": "A"})})

        assert exc_info.value.status_code == 500
        assert "Missing answer for question 'expected_q'" in str(exc_info.value)


# --- 5. MockDecisionClient Resilience & Normalization Tests ---


class TestMockDecisionClientResilience:
    """Verifies exception simulation and probability normalization in MockDecisionClient."""

    @pytest.mark.asyncio
    async def test_mock_raises_canned_exception_instance(self):
        mock = MockDecisionClient(canned_answers={"q": DecisionAPIError("Downstream failed", status_code=503)})
        with pytest.raises(DecisionAPIError) as exc_info:
            await mock.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.status_code == 503
        assert "Downstream failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_mock_raises_canned_exception_class(self):
        mock = MockDecisionClient(canned_answers={"q": DecisionTimeoutError})
        with pytest.raises(DecisionTimeoutError):
            await mock.evaluate("state", {"q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

    @pytest.mark.asyncio
    async def test_mock_raises_wildcard_exception(self):
        mock = MockDecisionClient(canned_answers={"*": DecisionRateLimitError("Global limit", retry_after=5.0)})
        with pytest.raises(DecisionRateLimitError) as exc_info:
            await mock.evaluate("state", {"any_q": ChoiceQuestion(instructions="i", criteria={"a": "b"})})

        assert exc_info.value.retry_after == 5.0

    @pytest.mark.asyncio
    async def test_mock_score_fallback_probabilities_sum_to_one(self):
        mock = MockDecisionClient()
        for count in [1, 2, 3, 4, 5, 8]:
            criteria = [f"level_{i}" for i in range(count)]
            res = await mock.evaluate("state", {"s": ScoreQuestion(instructions="score", criteria=criteria)})
            s_ans = res.answers["s"]
            assert isinstance(s_ans, ScoreAnswer)
            probs_sum = sum(s_ans.probabilities.values())
            assert math.isclose(probs_sum, 1.0, rel_tol=1e-3), f"Failed for count {count}: {probs_sum}"
            for k, p in s_ans.probabilities.items():
                assert 0.0 <= p <= 1.0

    @pytest.mark.asyncio
    async def test_mock_choice_fallback_probabilities_sum_to_one(self):
        mock = MockDecisionClient()
        for count in [1, 2, 3, 5]:
            criteria = {f"opt_{i}": f"Desc {i}" for i in range(count)}
            res = await mock.evaluate("state", {"c": ChoiceQuestion(instructions="choice", criteria=criteria)})
            c_ans = res.answers["c"]
            assert isinstance(c_ans, ChoiceAnswer)
            probs_sum = sum(c_ans.probabilities.values())
            assert math.isclose(probs_sum, 1.0, rel_tol=1e-3), f"Failed for count {count}: {probs_sum}"
            for k, p in c_ans.probabilities.items():
                assert 0.0 <= p <= 1.0

    @pytest.mark.asyncio
    async def test_mock_call_recording_and_clearing(self):
        mock = MockDecisionClient()
        assert len(mock.recorded_calls) == 0

        await mock.evaluate("state_1", {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})})
        assert len(mock.recorded_calls) == 1
        assert mock.recorded_calls[0]["state"] == "state_1"

        mock.clear_recorded_calls()
        assert len(mock.recorded_calls) == 0


# --- 6. Decision Models Schema Constraints Tests ---


class TestDecisionModelsValidation:
    """Verifies min_length criteria constraints and probability bounds in models."""

    def test_choice_question_rejects_empty_criteria(self):
        with pytest.raises(ValidationError):
            ChoiceQuestion(instructions="route", criteria={})

    def test_score_question_rejects_empty_criteria(self):
        with pytest.raises(ValidationError):
            ScoreQuestion(instructions="grade", criteria=[])

    def test_choice_question_accepts_non_empty_criteria(self):
        q = ChoiceQuestion(instructions="route", criteria={"coder": "Code"})
        assert len(q.criteria) == 1

    def test_score_question_accepts_non_empty_criteria(self):
        q = ScoreQuestion(instructions="grade", criteria=["pass", "fail"])
        assert len(q.criteria) == 2

    def test_choice_answer_probabilities_bounds(self):
        # Valid bounds
        ans = ChoiceAnswer(choice="a", probabilities={"a": 0.0, "b": 1.0})
        assert ans.probabilities["a"] == 0.0
        assert ans.probabilities["b"] == 1.0

        # Negative probability
        with pytest.raises(ValidationError):
            ChoiceAnswer(choice="a", probabilities={"a": -0.01})

        # Probability > 1.0
        with pytest.raises(ValidationError):
            ChoiceAnswer(choice="a", probabilities={"a": 1.001})

    def test_score_answer_probabilities_bounds(self):
        # Valid bounds
        ans = ScoreAnswer(score=3.0, probabilities={"low": 0.2, "high": 0.8})
        assert ans.probabilities["low"] == 0.2

        # Negative probability
        with pytest.raises(ValidationError):
            ScoreAnswer(score=1.0, probabilities={"low": -0.5})

        # Probability > 1.0
        with pytest.raises(ValidationError):
            ScoreAnswer(score=1.0, probabilities={"high": 2.5})

    def test_models_support_value_alias_fallback(self):
        # NoulAnswer from value
        na = NoulAnswer.model_validate({"value": 0.72})
        assert na.noul == 0.72
        assert na.decision is True

        # ScoreAnswer from value
        sa = ScoreAnswer.model_validate({"value": 3.2})
        assert sa.score == 3.2

        # ChoiceAnswer from value
        ca = ChoiceAnswer.model_validate({"value": "opt_x"})
        assert ca.choice == "opt_x"


# --- 7. Convenience Evaluator Methods & Lifecycle Tests ---


class TestClientConvenienceAndLifecycle:
    """Verifies evaluate_choice, evaluate_noul, evaluate_score, and resource lifecycle."""

    @pytest.mark.asyncio
    async def test_convenience_methods_on_mock(self):
        mock = MockDecisionClient()

        c = await mock.evaluate_choice("state", "c", {"a": "A"})
        assert isinstance(c, ChoiceAnswer)
        assert c.choice == "a"

        n = await mock.evaluate_noul("state", "n", threshold=0.5)
        assert isinstance(n, NoulAnswer)
        assert n.decision is True

        s = await mock.evaluate_score("state", "s", ["l1", "l2"])
        assert isinstance(s, ScoreAnswer)
        assert s.score == 1.0

    @pytest.mark.asyncio
    async def test_convenience_methods_on_cloudflare(self):
        def handler(request: httpx.Request) -> httpx.Response:
            import json
            req_data = json.loads(request.content)
            q_keys = list(req_data.get("questions", {}).keys())
            key = q_keys[0] if q_keys else "q"
            if "choice" in key:
                ans = {key: {"choice": "coder", "confidence": 0.9}}
            elif "noul" in key:
                ans = {key: {"noul": 0.95, "decision": True}}
            else:
                ans = {key: {"score": 4.0}}
            return httpx.Response(200, json={"success": True, "result": {"answers": ans}})

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        c = await client.evaluate_choice("state", "c", {"coder": "Code"})
        assert c.choice == "coder"

        n = await client.evaluate_noul("state", "n", threshold=0.5)
        assert n.noul == 0.95

        s = await client.evaluate_score("state", "s", ["c1", "c2"])
        assert s.score == 4.0

    @pytest.mark.asyncio
    async def test_client_context_manager_lifecycle(self):
        async with CloudflareClefClient("acc", "tok") as client:
            assert client._client.is_closed is False
        assert client._client.is_closed is True

    @pytest.mark.asyncio
    async def test_external_client_preserved_on_close(self):
        ext_client = httpx.AsyncClient()
        cf = CloudflareClefClient("acc", "tok", client=ext_client)
        await cf.close()
        assert ext_client.is_closed is False
        await ext_client.aclose()
