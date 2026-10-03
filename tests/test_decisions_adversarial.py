"""Adversarial stress harness for Decision Engine resilience and edge cases.

Specifically stress-tests:
1. Retry-After header parsing (RFC 7231, RFC 850, asctime, timezones, malformed, negative, extreme values).
2. HTML / non-JSON responses across 401, 403, 429, 500, 502, 503, 504, 200 (avoiding JSONDecodeError).
3. MockDecisionClient exception raising (instances, classes, wildcards, question-level overrides).
4. Model boundary validations and probability constraints.
"""

from __future__ import annotations

import asyncio
import math
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


# ============================================================================
# 1. Adversarial Retry-After Parsing & Fallback Harness
# ============================================================================


class TestAdversarialRetryAfterParsing:
    """Stress tests Retry-After parsing with valid RFC dates, malformed inputs, and extreme values."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "header_value",
        [
            # Standard RFC 7231 / RFC 1123 future dates
            "Wed, 21 Oct 2026 07:28:00 GMT",
            "Fri, 31 Dec 2027 23:59:59 GMT",
            # Single-digit day
            "Wed, 7 Oct 2026 07:28:00 GMT",
            # RFC 850 format
            "Wednesday, 21-Oct-26 07:28:00 GMT",
            # ANSI C asctime() format
            "Wed Oct 21 07:28:00 2026",
            # Non-GMT timezones
            "Wed, 21 Oct 2026 09:28:00 +0200",
            "Wed, 21 Oct 2026 02:28:00 -0500",
            # Leap day future date
            "Sun, 29 Feb 2032 12:00:00 GMT",
            # Positive numeric seconds (integer & float)
            "0",
            "0.0",
            "0.001",
            "1",
            "60",
            "120.5",
            "3600",
            # Past dates (should safely clamp to >= 0.0 or apply jitter fallback)
            "Sun, 06 Nov 1994 08:49:37 GMT",
            "Thu, 01 Jan 1970 00:00:00 GMT",
            # Negative numeric values (should trigger fallback)
            "-1",
            "-0.0",
            "-500",
            "-0.0001",
            # Special float representations (should trigger fallback)
            "NaN",
            "nan",
            "+nan",
            "-nan",
            "Infinity",
            "-Infinity",
            "inf",
            "-inf",
            # Scientific notation
            "1e2",
            "1e-5",
            # Extreme strings
            "9" * 100,
            # Malformed string representations (should trigger fallback without raising ValueError)
            "",
            "   ",
            "\t\n\r",
            "invalid-string",
            "not a date",
            "Mon, 99 Dec 9999 99:99:99 GMT",
            '"60"',
            "120, 240",
            "60s",
            "null",
            "None",
            "\x00\x01\x02",
            "<script>alert(1)</script>",
            "'; DROP TABLE retry;--",
        ],
    )
    async def test_retry_after_stress_matrix(self, header_value: str):
        """Verify that every Retry-After header variant is parsed safely without unhandled exceptions."""
        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(429, headers={"Retry-After": header_value})
                )
            ),
            max_retries=0,
        )

        with pytest.raises(DecisionRateLimitError) as exc_info:
            await client.evaluate(
                "test state",
                {"q": ChoiceQuestion(instructions="route", criteria={"a": "desc A"})},
            )

        err = exc_info.value
        assert err.status_code == 429
        assert err.retry_after is not None
        assert not math.isnan(err.retry_after)
        assert not math.isinf(err.retry_after)
        assert err.retry_after >= 0.0

    @pytest.mark.asyncio
    async def test_missing_retry_after_header_applies_fallback(self):
        """Verify that missing Retry-After header applies exponential backoff fallback."""
        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(429))
            ),
            max_retries=0,
        )
        with pytest.raises(DecisionRateLimitError) as exc_info:
            await client.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="route", criteria={"a": "desc A"})},
            )
        assert exc_info.value.retry_after is not None
        assert exc_info.value.retry_after > 0.0

    @pytest.mark.asyncio
    async def test_retry_loop_clamping_and_success_after_rate_limit(self, monkeypatch):
        """Verify retry loop clamps excessive sleep times to 5.0s and retries successfully."""
        slept_times: list[float] = []

        async def mock_sleep(seconds: float):
            slept_times.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # Return very large Retry-After: 99999.0s
                return httpx.Response(429, headers={"Retry-After": "99999.0"})
            return httpx.Response(
                200,
                json={"success": True, "result": {"answers": {"q": "opt_ok"}}},
            )

        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=1,
        )

        res = await client.evaluate(
            "state",
            {"q": ChoiceQuestion(instructions="route", criteria={"opt_ok": "OK"})},
        )
        assert attempts == 2
        assert res.answers["q"].choice == "opt_ok"
        # Must have clamped sleep to 5.0 seconds
        assert len(slept_times) == 1
        assert slept_times[0] == 5.0


# ============================================================================
# 2. Adversarial HTML & Non-JSON Error Responses Harness
# ============================================================================


class TestAdversarialHtmlAndNonJsonResponseHandling:
    """Stress tests HTML, XML, raw text, and binary responses across all HTTP status codes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status_code", "expected_exc_type"),
        [
            (401, DecisionAuthenticationError),
            (403, DecisionAuthenticationError),
            (429, DecisionRateLimitError),
            (500, DecisionAPIError),
            (502, DecisionAPIError),
            (503, DecisionAPIError),
            (504, DecisionAPIError),
        ],
    )
    async def test_html_error_pages_safe_json_handling(
        self, status_code: int, expected_exc_type: type[DecisionError]
    ):
        """Verify that typical HTML gateway/proxy error pages do not raise JSONDecodeError."""
        html_payload = f"""<!DOCTYPE html>
<html>
<head><title>{status_code} Error</title></head>
<body>
<center><h1>{status_code} Error from Cloudflare Edge / Nginx</h1></center>
<hr><center>cloudflare-nginx</center>
<p>Ray ID: 7f8a9b0c1d2e3f4a &bull; IP: 1.2.3.4</p>
</body>
</html>"""

        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(status_code, text=html_payload)
                )
            ),
            max_retries=0,
        )

        with pytest.raises(expected_exc_type) as exc_info:
            await client.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )

        err = exc_info.value
        assert isinstance(err, DecisionError)
        assert err.status_code == status_code
        # Ensure error message captures snippet of HTML without crashing
        assert len(err.errors) > 0
        assert "Cloudflare" in str(err) or f"{status_code}" in str(err)

    @pytest.mark.asyncio
    async def test_empty_body_across_error_statuses(self):
        """Verify empty body responses across error codes do not raise JSONDecodeError."""
        for code, exc_cls in [
            (401, DecisionAuthenticationError),
            (403, DecisionAuthenticationError),
            (429, DecisionRateLimitError),
            (500, DecisionAPIError),
            (502, DecisionAPIError),
            (504, DecisionAPIError),
        ]:
            client = CloudflareClefClient(
                account_id="test_acc",
                api_token="test_tok",
                client=httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda r: httpx.Response(code, content=b""))
                ),
                max_retries=0,
            )
            with pytest.raises(exc_cls) as exc_info:
                await client.evaluate(
                    "state",
                    {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
                )
            assert exc_info.value.status_code == code

    @pytest.mark.asyncio
    async def test_massive_html_body_truncation(self):
        """Verify that massive HTML bodies (e.g. 500KB) are safely truncated to 500 chars."""
        massive_html = "<html>" + ("A" * 500_000) + "</html>"
        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(502, text=massive_html)
                )
            ),
            max_retries=0,
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )
        err = exc_info.value
        assert err.status_code == 502
        # Verify message truncated
        assert len(err.errors[0]["message"]) <= 500

    @pytest.mark.asyncio
    async def test_binary_garbage_response(self):
        """Verify raw non-UTF8 binary data does not crash the client."""
        binary_garbage = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\xff\xfe\x00\x01\xfa\xfb"
        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(502, content=binary_garbage)
                )
            ),
            max_retries=0,
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_status_200_with_html_captive_portal(self):
        """Verify captive portal returning 200 with HTML raises DecisionAPIError rather than JSONDecodeError."""
        captive_html = "<!DOCTYPE html><html><body>Please log in to Airport Wi-Fi</body></html>"
        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, text=captive_html)
                )
            ),
            max_retries=0,
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )
        assert exc_info.value.status_code == 500
        assert "Missing answer for question 'q'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_status_200_with_truncated_malformed_json(self):
        """Verify truncated JSON stream returning 200 raises DecisionAPIError."""
        truncated_json = b'{"success": true, "result": {"answers": {"q": {'
        client = CloudflareClefClient(
            account_id="test_acc",
            api_token="test_tok",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, content=truncated_json)
                )
            ),
            max_retries=0,
        )
        with pytest.raises(DecisionAPIError) as exc_info:
            await client.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )
        assert exc_info.value.status_code == 500


# ============================================================================
# 3. Adversarial MockDecisionClient Exception Simulation Harness
# ============================================================================


class TestAdversarialMockDecisionClient:
    """Stress tests exception simulation in MockDecisionClient with various types and overrides."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc_instance",
        [
            DecisionAPIError("API down", status_code=502),
            DecisionRateLimitError("Rate limited", retry_after=42.0),
            DecisionAuthenticationError("Unauthorized", status_code=401),
            DecisionTimeoutError("Client timeout"),
            DecisionClientError("Client validation error"),
            RuntimeError("Arbitrary runtime error"),
            ValueError("Value error"),
            KeyError("Missing key error"),
            OSError("Network socket dropped"),
        ],
    )
    async def test_mock_raises_canned_exception_instances(self, exc_instance: Exception):
        """Verify any Exception instance can be simulated and raised."""
        mock = MockDecisionClient(canned_answers={"q": exc_instance})
        with pytest.raises(type(exc_instance)) as exc_info:
            await mock.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )
        assert exc_info.value is exc_instance

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc_class",
        [
            DecisionAPIError,
            DecisionRateLimitError,
            DecisionAuthenticationError,
            DecisionTimeoutError,
            DecisionClientError,
            RuntimeError,
            ValueError,
            ZeroDivisionError,
            TypeError,
        ],
    )
    async def test_mock_raises_canned_exception_classes(self, exc_class: type[Exception]):
        """Verify Exception classes can be passed and dynamically instantiated."""
        mock = MockDecisionClient(canned_answers={"q": exc_class})
        with pytest.raises(exc_class) as exc_info:
            await mock.evaluate(
                "state",
                {"q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )
        assert isinstance(exc_info.value, exc_class)

    @pytest.mark.asyncio
    async def test_mock_wildcard_overridden_by_specific_answer(self):
        """Verify specific question answer takes precedence over wildcard exception."""
        mock = MockDecisionClient(
            canned_answers={
                "*": DecisionAPIError("Global failure", status_code=500),
                "safe_q": ChoiceAnswer(choice="opt_safe"),
            }
        )

        # safe_q alone succeeds
        res = await mock.evaluate(
            "state",
            {"safe_q": ChoiceQuestion(instructions="i", criteria={"opt_safe": "Safe"})},
        )
        assert res.answers["safe_q"].choice == "opt_safe"

        # other_q triggers wildcard exception
        with pytest.raises(DecisionAPIError):
            await mock.evaluate(
                "state",
                {"other_q": ChoiceQuestion(instructions="i", criteria={"a": "A"})},
            )

    @pytest.mark.asyncio
    async def test_mock_multi_question_partial_exception_fails_call(self):
        """Verify multi-question evaluation fails if any requested question raises."""
        mock = MockDecisionClient(
            canned_answers={
                "q1": ChoiceAnswer(choice="a"),
                "q2": DecisionTimeoutError("q2 timed out"),
            }
        )
        with pytest.raises(DecisionTimeoutError):
            await mock.evaluate(
                "state",
                {
                    "q1": ChoiceQuestion(instructions="i1", criteria={"a": "A"}),
                    "q2": ChoiceQuestion(instructions="i2", criteria={"b": "B"}),
                },
            )

    @pytest.mark.asyncio
    async def test_mock_score_and_choice_probability_normalization_invariance(self):
        """Verify default mock probabilities are strictly within [0.0, 1.0] and sum to 1.0."""
        mock = MockDecisionClient()

        # Test various choice criteria sizes
        for n in [1, 2, 3, 4, 7, 10]:
            q = ChoiceQuestion(
                instructions="i",
                criteria={f"c_{i}": f"Desc {i}" for i in range(n)},
            )
            res = await mock.evaluate("state", {"q": q})
            ans = res.answers["q"]
            assert isinstance(ans, ChoiceAnswer)
            for opt, p in ans.probabilities.items():
                assert 0.0 <= p <= 1.0, f"Option {opt} probability {p} out of bounds"
            prob_sum = sum(ans.probabilities.values())
            assert math.isclose(prob_sum, 1.0, rel_tol=1e-3)

        # Test various score criteria sizes
        for n in [1, 2, 3, 5, 8, 12]:
            q = ScoreQuestion(
                instructions="i",
                criteria=[f"level_{i}" for i in range(n)],
            )
            res = await mock.evaluate("state", {"q": q})
            ans = res.answers["q"]
            assert isinstance(ans, ScoreAnswer)
            for lvl, p in ans.probabilities.items():
                assert 0.0 <= p <= 1.0, f"Level {lvl} probability {p} out of bounds"
            prob_sum = sum(ans.probabilities.values())
            assert math.isclose(prob_sum, 1.0, rel_tol=1e-3)


# ============================================================================
# 4. Adversarial Schema Validation & Edge Constraints
# ============================================================================


class TestAdversarialSchemaValidation:
    """Stress tests question schemas and answer probability validation."""

    def test_probability_boundary_values(self):
        """Verify exact boundary values 0.0 and 1.0 are accepted."""
        ans = ChoiceAnswer(
            choice="a",
            probabilities={"a": 1.0, "b": 0.0},
        )
        assert ans.probabilities["a"] == 1.0
        assert ans.probabilities["b"] == 0.0

    def test_probability_strictly_rejects_negative_and_excessive(self):
        """Verify values outside [0.0, 1.0] are strictly rejected."""
        # Epsilon negative
        with pytest.raises(ValidationError):
            ChoiceAnswer(choice="a", probabilities={"a": -1e-9})

        # Epsilon > 1.0
        with pytest.raises(ValidationError):
            ChoiceAnswer(choice="a", probabilities={"a": 1.0000001})

        # ScoreAnswer epsilon negative
        with pytest.raises(ValidationError):
            ScoreAnswer(score=1.0, probabilities={"lvl": -0.0001})

        # ScoreAnswer epsilon > 1.0
        with pytest.raises(ValidationError):
            ScoreAnswer(score=1.0, probabilities={"lvl": 1.00001})

    def test_empty_criteria_strictly_rejected(self):
        """Verify empty criteria dictionaries or lists are rejected with ValidationError."""
        with pytest.raises(ValidationError):
            ChoiceQuestion(instructions="i", criteria={})

        with pytest.raises(ValidationError):
            ScoreQuestion(instructions="i", criteria=[])

    def test_noul_threshold_boundaries(self):
        """Verify NoulQuestion accepts 0.0 and 1.0 thresholds but rejects <0 or >1."""
        q0 = NoulQuestion(instructions="i", threshold=0.0)
        assert q0.threshold == 0.0

        q1 = NoulQuestion(instructions="i", threshold=1.0)
        assert q1.threshold == 1.0

        with pytest.raises(ValidationError):
            NoulQuestion(instructions="i", threshold=-0.01)

        with pytest.raises(ValidationError):
            NoulQuestion(instructions="i", threshold=1.01)
