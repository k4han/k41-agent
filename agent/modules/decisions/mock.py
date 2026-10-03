"""Deterministic mock client for offline testing and verification."""

from __future__ import annotations

from typing import Any

from agent.modules.decisions.models import (
    AnswerUnion,
    BaseQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionResult,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from agent.modules.decisions.ports import DecisionClient


class MockDecisionClient(DecisionClient):
    """Deterministic mock client for offline tests, simulations, and router verification."""

    def __init__(
        self,
        canned_answers: dict[str, Any] | None = None,
        latency_ms: float = 35.0,
        default_model: str = "@cf/cloudflare/clef-flash",
    ) -> None:
        self.canned_answers: dict[str, Any] = dict(canned_answers) if canned_answers else {}
        self.latency_ms = latency_ms
        self.default_model = default_model
        self.recorded_calls: list[dict[str, Any]] = []

    def set_canned_answer(self, question_id: str, answer: Any) -> None:
        """Register or overwrite a canned answer for a specific question ID."""
        self.canned_answers[question_id] = answer

    def clear_recorded_calls(self) -> None:
        """Reset the call recording history."""
        self.recorded_calls.clear()

    async def evaluate(
        self,
        state: str | dict[str, Any],
        questions: dict[str, BaseQuestion],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> DecisionResult:
        """Return deterministic mock responses and record call parameters."""
        self.recorded_calls.append(
            {
                "state": state,
                "questions": questions,
                "model": model,
                "kwargs": kwargs,
            }
        )

        answers: dict[str, AnswerUnion] = {}

        for q_id, q_def in questions.items():
            if q_id in self.canned_answers or "*" in self.canned_answers:
                canned = self.canned_answers.get(q_id, self.canned_answers.get("*"))
                if isinstance(canned, Exception):
                    raise canned
                if isinstance(canned, type) and issubclass(canned, Exception):
                    try:
                        raise canned()
                    except TypeError:
                        raise canned(f"Simulated {canned.__name__}")
                if isinstance(canned, (ChoiceAnswer, NoulAnswer, ScoreAnswer)):
                    answers[q_id] = canned
                elif isinstance(q_def, ChoiceQuestion):
                    if isinstance(canned, str):
                        canned = {"choice": canned, "confidence": 1.0}
                    answers[q_id] = ChoiceAnswer.model_validate(canned)
                elif isinstance(q_def, NoulQuestion):
                    if isinstance(canned, bool):
                        canned = {
                            "noul": 1.0 if canned else 0.0,
                            "decision": canned,
                            "threshold": q_def.threshold,
                            "confidence": 1.0,
                        }
                    elif isinstance(canned, (int, float)):
                        canned = {"noul": float(canned), "threshold": q_def.threshold}
                    answers[q_id] = NoulAnswer.model_validate(canned)
                elif isinstance(q_def, ScoreQuestion):
                    if isinstance(canned, (int, float)):
                        canned = {"score": float(canned), "confidence": 1.0}
                    answers[q_id] = ScoreAnswer.model_validate(canned)
                else:
                    # Fallback generic validation
                    answers[q_id] = ChoiceAnswer.model_validate(canned)
            else:
                # Sensible default answers for unconfigured questions
                if isinstance(q_def, ChoiceQuestion):
                    first_opt = next(iter(q_def.criteria.keys()), "default")
                    if len(q_def.criteria) <= 1:
                        probs = {first_opt: 1.0}
                    else:
                        other_count = len(q_def.criteria) - 1
                        other_prob = round(0.05 / other_count, 4)
                        first_prob = round(1.0 - (other_prob * other_count), 4)
                        probs = {
                            k: (first_prob if k == first_opt else other_prob)
                            for k in q_def.criteria
                        }
                    answers[q_id] = ChoiceAnswer(
                        choice=first_opt,
                        probabilities=probs,
                        confidence=probs.get(first_opt, 0.95),
                    )
                elif isinstance(q_def, NoulQuestion):
                    noul = 0.88
                    answers[q_id] = NoulAnswer(
                        noul=noul,
                        decision=noul >= q_def.threshold,
                        confidence=max(noul, 1.0 - noul),
                    )
                elif isinstance(q_def, ScoreQuestion):
                    selected = q_def.criteria[-1] if q_def.criteria else "high"
                    if len(q_def.criteria) <= 1:
                        probs = {selected: 1.0}
                    else:
                        other_count = len(q_def.criteria) - 1
                        other_prob = round(0.10 / other_count, 4)
                        selected_prob = round(1.0 - (other_prob * other_count), 4)
                        probs = {
                            k: (selected_prob if k == selected else other_prob)
                            for k in q_def.criteria
                        }
                    answers[q_id] = ScoreAnswer(
                        score=1.0,
                        selected_level=selected,
                        probabilities=probs,
                        confidence=probs.get(selected, 0.90),
                    )

        return DecisionResult(
            model=model or self.default_model,
            answers=answers,
            latency_ms=self.latency_ms,
            raw_response={"mock": True, "recorded_calls_count": len(self.recorded_calls)},
        )
