"""Adversarial and regression test suite for decision schemas, models, and clients."""

import asyncio
import math
from typing import Any
import httpx
from pydantic import TypeAdapter, ValidationError
import pytest

from agent.modules.decisions import (
    BaseAnswer,
    BaseQuestion,
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
    QuestionUnion,
    ScoreAnswer,
    ScoreQuestion,
    AnswerUnion,
)


class TestQuestionSchemas:
    """Stress tests for decision question schemas."""

    def test_choice_question_basic(self):
        q = ChoiceQuestion(
            instructions="Route the user request",
            criteria={"coder": "Code generation", "qa": "Testing and validation"},
        )
        assert q.type == "choice"
        assert len(q.criteria) == 2
        assert q.instructions == "Route the user request"

    def test_noul_question_threshold_edges(self):
        # Default threshold
        q_def = NoulQuestion(instructions="Is safe?")
        assert q_def.threshold == 0.5

        # Boundary 0.0 and 1.0
        q_0 = NoulQuestion(instructions="Is safe?", threshold=0.0)
        assert q_0.threshold == 0.0

        q_1 = NoulQuestion(instructions="Is safe?", threshold=1.0)
        assert q_1.threshold == 1.0

        # High precision float
        q_prec = NoulQuestion(instructions="Is safe?", threshold=0.30000000000000004)
        assert math.isclose(q_prec.threshold, 0.3)

        # Invalid threshold out of bounds
        with pytest.raises(ValidationError):
            NoulQuestion(instructions="Is safe?", threshold=-0.001)

        with pytest.raises(ValidationError):
            NoulQuestion(instructions="Is safe?", threshold=1.001)

    def test_score_question_basic(self):
        q = ScoreQuestion(
            instructions="Grade answer quality",
            criteria=["poor", "acceptable", "good", "excellent"],
        )
        assert q.type == "score"
        assert q.criteria == ["poor", "acceptable", "good", "excellent"]

    def test_unicode_and_special_characters(self):
        vietnamese_prompt = (
            "Đánh giá độ phù hợp của agent xử lý yêu cầu: 1. Lập trình viên "
            "2. Nhà nghiên cứu dữ liệu 🚀🔥💻"
        )
        criteria = {
            "lập_trình": "Xử lý mã nguồn, kiểm thử, refactor",
            "nghiên_cứu": "Tổng hợp tài liệu và phân tích dữ liệu 📊",
        }
        q = ChoiceQuestion(instructions=vietnamese_prompt, criteria=criteria)
        dumped = q.model_dump_json()
        restored = ChoiceQuestion.model_validate_json(dumped)
        assert restored.instructions == vietnamese_prompt
        assert restored.criteria["lập_trình"] == criteria["lập_trình"]

    def test_empty_criteria_rejected_by_schema(self):
        # Empty criteria rejected by schema (min_length=1 enforced)
        with pytest.raises(ValidationError):
            ChoiceQuestion(instructions="route", criteria={})
        with pytest.raises(ValidationError):
            ScoreQuestion(instructions="rate", criteria=[])

    def test_question_union_serialization_round_trip(self):
        adapter = TypeAdapter(QuestionUnion)

        cq = ChoiceQuestion(instructions="Choose agent", criteria={"a": "A", "b": "B"})
        restored_cq = adapter.validate_python(cq.model_dump())
        assert isinstance(restored_cq, ChoiceQuestion)
        assert restored_cq.type == "choice"

        nq = NoulQuestion(instructions="Verify boolean", threshold=0.75)
        restored_nq = adapter.validate_json(nq.model_dump_json())
        assert isinstance(restored_nq, NoulQuestion)
        assert restored_nq.threshold == 0.75

        sq = ScoreQuestion(instructions="Evaluate rubric", criteria=["low", "high"])
        restored_sq = adapter.validate_python(sq.model_dump())
        assert isinstance(restored_sq, ScoreQuestion)
        assert restored_sq.criteria == ["low", "high"]


class TestAnswerSchemas:
    """Stress tests for answer schemas, automated confidence, and edge cases."""

    def test_choice_answer_auto_confidence(self):
        # Auto-derives confidence from probabilities[choice]
        ans = ChoiceAnswer.model_validate({
            "choice": "coder",
            "probabilities": {"coder": 0.88, "qa": 0.12},
        })
        assert ans.choice == "coder"
        assert ans.confidence == 0.88

    def test_choice_answer_manual_confidence_override(self):
        ans = ChoiceAnswer(
            choice="coder",
            probabilities={"coder": 0.5, "qa": 0.5},
            confidence=0.99,
        )
        assert ans.confidence == 0.99

    def test_choice_answer_probabilities_sum_deviations(self):
        # Floating point rounding or non-normalized sums should not crash schema
        ans = ChoiceAnswer(
            choice="coder",
            probabilities={"coder": 0.6, "qa": 0.6},  # sum = 1.2
            confidence=0.6,
        )
        assert ans.probabilities["coder"] == 0.6
        assert ans.confidence == 0.6

    def test_choice_answer_choice_not_in_probabilities(self):
        ans = ChoiceAnswer(choice="unknown", probabilities={"coder": 0.9})
        assert ans.choice == "unknown"
        assert ans.confidence == 0.0

    def test_noul_answer_auto_decision_and_confidence(self):
        # High noul >= threshold -> decision True, confidence = 0.9
        a1 = NoulAnswer.model_validate({"noul": 0.9, "threshold": 0.5})
        assert a1.decision is True
        assert a1.confidence == 0.9

        # Low noul < threshold -> decision False, confidence = 1.0 - 0.2 = 0.8
        a2 = NoulAnswer.model_validate({"noul": 0.2, "threshold": 0.5})
        assert a2.decision is False
        assert a2.confidence == 0.8

        # Ambiguous noul = 0.5 -> decision True (>= 0.5), confidence = 0.5
        a3 = NoulAnswer.model_validate({"noul": 0.5, "threshold": 0.5})
        assert a3.decision is True
        assert a3.confidence == 0.5

    def test_noul_answer_threshold_edges(self):
        # Threshold = 0.0: 0.0 >= 0.0 is True
        a_0 = NoulAnswer.model_validate({"noul": 0.0, "threshold": 0.0})
        assert a_0.decision is True
        assert a_0.confidence == 1.0

        # Threshold = 1.0: 1.0 >= 1.0 is True, 0.999 is False
        a_1_hit = NoulAnswer.model_validate({"noul": 1.0, "threshold": 1.0})
        assert a_1_hit.decision is True

        a_1_miss = NoulAnswer.model_validate({"noul": 0.999999, "threshold": 1.0})
        assert a_1_miss.decision is False

        # Precision epsilon
        eps = 1e-12
        a_eps_hi = NoulAnswer.model_validate({"noul": 0.75 + eps, "threshold": 0.75})
        assert a_eps_hi.decision is True
        a_eps_lo = NoulAnswer.model_validate({"noul": 0.75 - eps, "threshold": 0.75})
        assert a_eps_lo.decision is False

    def test_noul_answer_bounds_and_invalid_values(self):
        with pytest.raises(ValidationError):
            NoulAnswer(noul=-0.01)

        with pytest.raises(ValidationError):
            NoulAnswer(noul=1.01)

        with pytest.raises(ValidationError):
            NoulAnswer.model_validate({"noul": float("nan")})

        with pytest.raises(ValidationError):
            NoulAnswer.model_validate({"noul": float("inf")})

    def test_score_answer_auto_fields(self):
        # Auto-infers selected_level and confidence from max probability
        ans = ScoreAnswer.model_validate({
            "score": 3.8,
            "probabilities": {"poor": 0.05, "fair": 0.15, "good": 0.80},
        })
        assert ans.score == 3.8
        assert ans.selected_level == "good"
        assert ans.confidence == 0.80

    def test_score_answer_empty_probabilities(self):
        ans = ScoreAnswer(score=2.0)
        assert ans.selected_level is None
        assert ans.confidence == 0.0

    def test_answer_union_serialization_round_trip(self):
        adapter = TypeAdapter(AnswerUnion)

        ca = ChoiceAnswer(choice="opt_a", confidence=0.75)
        restored_ca = adapter.validate_python(ca.model_dump())
        assert isinstance(restored_ca, ChoiceAnswer)
        assert restored_ca.choice == "opt_a"

        na = NoulAnswer(noul=0.88, decision=True, confidence=0.88)
        restored_na = adapter.validate_json(na.model_dump_json())
        assert isinstance(restored_na, NoulAnswer)
        assert restored_na.decision is True

        sa = ScoreAnswer(score=4.5, selected_level="expert", confidence=0.92)
        restored_sa = adapter.validate_python(sa.model_dump())
        assert isinstance(restored_sa, ScoreAnswer)
        assert restored_sa.selected_level == "expert"

    def test_decision_result_heterogeneous_answers_round_trip(self):
        res = DecisionResult(
            model="@cf/cloudflare/clef-flash",
            answers={
                "q_choice": ChoiceAnswer(choice="coder", confidence=0.9),
                "q_noul": NoulAnswer(noul=0.85, decision=True, confidence=0.85),
                "q_score": ScoreAnswer(score=4.0, selected_level="high", confidence=0.95),
            },
            latency_ms=38.4,
            raw_response={"mock": True},
        )

        # JSON round-trip
        json_str = res.model_dump_json()
        restored = DecisionResult.model_validate_json(json_str)
        assert restored.model == "@cf/cloudflare/clef-flash"
        assert isinstance(restored.answers["q_choice"], ChoiceAnswer)
        assert isinstance(restored.answers["q_noul"], NoulAnswer)
        assert isinstance(restored.answers["q_score"], ScoreAnswer)
        assert restored.answers["q_choice"].choice == "coder"
        assert restored.answers["q_noul"].decision is True
        assert restored.answers["q_score"].score == 4.0


class TestExceptionHierarchy:
    """Verify exception inheritance and attributes."""

    def test_hierarchy_inheritance(self):
        assert issubclass(DecisionClientError, DecisionError)
        assert issubclass(DecisionAPIError, DecisionError)
        assert issubclass(DecisionAuthenticationError, DecisionAPIError)
        assert issubclass(DecisionRateLimitError, DecisionAPIError)
        assert issubclass(DecisionTimeoutError, DecisionError)

    def test_rate_limit_error_attributes(self):
        err = DecisionRateLimitError("Rate limited", retry_after=3.5, errors=[{"code": 429}])
        assert err.status_code == 429
        assert err.retry_after == 3.5
        assert len(err.errors) == 1


class TestMockDecisionClient:
    """Stress tests for deterministic MockDecisionClient."""

    @pytest.mark.asyncio
    async def test_mock_defaults(self):
        client = MockDecisionClient()
        res = await client.evaluate(
            "State text",
            {
                "c": ChoiceQuestion(instructions="Pick", criteria={"c1": "C1", "c2": "C2"}),
                "n": NoulQuestion(instructions="Is ok?"),
                "s": ScoreQuestion(instructions="Rate", criteria=["low", "high"]),
            },
        )
        assert isinstance(res.answers["c"], ChoiceAnswer)
        assert res.answers["c"].choice == "c1"
        assert isinstance(res.answers["n"], NoulAnswer)
        assert res.answers["n"].decision is True
        assert isinstance(res.answers["s"], ScoreAnswer)
        assert res.answers["s"].selected_level == "high"
        assert len(client.recorded_calls) == 1

    @pytest.mark.asyncio
    async def test_mock_canned_answers_shorthands(self):
        client = MockDecisionClient()
        client.set_canned_answer("c", "admin")
        client.set_canned_answer("n_bool", False)
        client.set_canned_answer("n_float", 0.15)
        client.set_canned_answer("s_num", 2.5)

        res = await client.evaluate(
            "State text",
            {
                "c": ChoiceQuestion(instructions="Pick", criteria={"admin": "A"}),
                "n_bool": NoulQuestion(instructions="Is ok?"),
                "n_float": NoulQuestion(instructions="Is prob?"),
                "s_num": ScoreQuestion(instructions="Rate", criteria=["low", "high"]),
            },
        )
        assert res.answers["c"].choice == "admin"
        assert res.answers["n_bool"].decision is False
        assert res.answers["n_float"].noul == 0.15
        assert res.answers["n_float"].decision is False
        assert res.answers["s_num"].score == 2.5

    @pytest.mark.asyncio
    async def test_mock_convenience_methods(self):
        client = MockDecisionClient()
        ca = await client.evaluate_choice("state", "pick", {"opt": "desc"})
        assert isinstance(ca, ChoiceAnswer)

        na = await client.evaluate_noul("state", "proceed", threshold=0.9)
        assert isinstance(na, NoulAnswer)

        sa = await client.evaluate_score("state", "score", ["l1", "l2"])
        assert isinstance(sa, ScoreAnswer)


class TestCloudflareClefClientEmpirical:
    """Empirical tests for CloudflareClefClient resilience and error paths."""

    @pytest.mark.asyncio
    async def test_empty_credentials_rejected(self):
        with pytest.raises(DecisionClientError):
            CloudflareClefClient(account_id="", api_token="valid_token")

        with pytest.raises(DecisionClientError):
            CloudflareClefClient(account_id="valid_account", api_token="")

    @pytest.mark.asyncio
    async def test_empty_questions_rejected(self):
        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        )
        with pytest.raises(DecisionClientError, match="At least one question"):
            await client.evaluate("state", {})

    @pytest.mark.asyncio
    async def test_auth_error_status_401(self):
        def auth_fail(request):
            return httpx.Response(401, json={"errors": [{"code": 10000, "message": "Invalid token"}]})

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(auth_fail)),
        )
        with pytest.raises(DecisionAuthenticationError) as exc_info:
            await client.evaluate("state", {"q": NoulQuestion(instructions="test")})
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_rate_limit_backoff_and_exhaustion(self):
        attempts = 0

        def rate_limit(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(429, headers={"Retry-After": "0.01"}, json={"errors": [{"message": "Rate limit"}]})

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            max_retries=2,
            client=httpx.AsyncClient(transport=httpx.MockTransport(rate_limit)),
        )
        with pytest.raises(DecisionRateLimitError) as exc_info:
            await client.evaluate("state", {"q": NoulQuestion(instructions="test")})
        assert exc_info.value.status_code == 429
        assert attempts == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        def timeout(request):
            raise httpx.ReadTimeout("Request timed out")

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            max_retries=1,
            client=httpx.AsyncClient(transport=httpx.MockTransport(timeout)),
        )
        with pytest.raises(DecisionTimeoutError):
            await client.evaluate("state", {"q": NoulQuestion(instructions="test")})

    @pytest.mark.asyncio
    async def test_standard_envelope_unpacking(self):
        envelope = {
            "success": True,
            "result": {
                "model": "clef-flash",
                "answers": {
                    "c": {"choice": "coder", "probabilities": {"coder": 0.95}, "confidence": 0.95},
                    "n": {"noul": 0.82},
                    "s": {"score": 3.5, "selected_level": "mid"},
                },
            },
        }

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=envelope))),
        )
        res = await client.evaluate(
            "state",
            {
                "c": ChoiceQuestion(instructions="pick", criteria={"coder": "c"}),
                "n": NoulQuestion(instructions="yes?"),
                "s": ScoreQuestion(instructions="rate", criteria=["low", "mid", "high"]),
            },
        )
        assert res.answers["c"].choice == "coder"
        assert res.answers["n"].decision is True
        assert res.answers["s"].score == 3.5

    @pytest.mark.asyncio
    async def test_scalar_answers_in_envelope_unpacked_successfully(self):
        """Verify that scalar answers (float/int) are unpacked successfully into typed answers."""
        scalar_envelope = {
            "success": True,
            "result": {
                "model": "clef-flash",
                "answers": {
                    "noul_q": 0.85,  # raw float
                    "score_q": 4.0,  # raw float
                },
            },
        }

        client = CloudflareClefClient(
            account_id="acc",
            api_token="tok",
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=scalar_envelope))),
        )

        res = await client.evaluate(
            "state",
            {
                "noul_q": NoulQuestion(instructions="is ready?"),
                "score_q": ScoreQuestion(instructions="grade", criteria=["poor", "mid", "high", "top"]),
            },
        )

        assert res.answers["noul_q"].noul == 0.85
        assert res.answers["noul_q"].decision is True
        assert res.answers["score_q"].score == 4.0
