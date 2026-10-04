"""High-level service managing decision client lifecycle, settings, and metrics."""

from __future__ import annotations

import logging
import time
from typing import Any

from agent.modules.decisions.providers import create_decision_client
from agent.modules.decisions.models import (
    BaseAnswer,
    BaseQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionClientError,
    DecisionResult,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from agent.modules.decisions.ports import DecisionClient
from agent.modules.decisions.settings import DecisionSettings, load_decision_settings

logger = logging.getLogger(__name__)


class DecisionService:
    """Manages DecisionClient provider instances, execution metrics, and convenience evaluations."""

    def __init__(
        self,
        client: DecisionClient | None = None,
        settings: DecisionSettings | None = None,
        config: Any = None,
    ) -> None:
        self._config = config if client is None else None
        self.settings = settings or load_decision_settings(config)
        self._client = client if client is not None else create_decision_client(self.settings)
        self._retired_clients: list[DecisionClient] = []

        self._total_evaluations: int = 0
        self._total_errors: int = 0
        self._total_latency_ms: float = 0.0

    @property
    def client(self) -> DecisionClient | None:
        """Refresh the selected client before each use, retaining in-flight clients."""
        if self._config is not None:
            settings = load_decision_settings(self._config)
            if settings != self.settings:
                client = create_decision_client(settings)
                if self._client is not None:
                    self._retired_clients.append(self._client)
                self._client = client
                self.settings = settings
        return self._client

    @property
    def is_configured(self) -> bool:
        """Check whether an underlying decision client is available."""
        return self.client is not None

    @property
    def total_evaluations(self) -> int:
        """Total number of successful evaluations executed."""
        return self._total_evaluations

    @property
    def total_errors(self) -> int:
        """Total number of evaluation attempts resulting in errors."""
        return self._total_errors

    @property
    def total_latency_ms(self) -> float:
        """Total accumulated latency in milliseconds across successful evaluations."""
        return self._total_latency_ms

    @property
    def average_latency_ms(self) -> float:
        """Mean evaluation latency in milliseconds."""
        if self._total_evaluations > 0:
            return self._total_latency_ms / self._total_evaluations
        return 0.0

    def get_metrics(self) -> dict[str, Any]:
        """Retrieve execution metrics dictionary."""
        return {
            "total_evaluations": self._total_evaluations,
            "total_errors": self._total_errors,
            "total_latency_ms": round(self._total_latency_ms, 2),
            "average_latency_ms": round(self.average_latency_ms, 2),
        }

    def reset_metrics(self) -> None:
        """Reset operational metrics."""
        self._total_evaluations = 0
        self._total_errors = 0
        self._total_latency_ms = 0.0

    def _require_client(self) -> DecisionClient:
        """Ensure an active client is configured or raise DecisionClientError."""
        client = self.client
        if client is None:
            raise DecisionClientError(
                "DecisionClient is not configured. Provider credentials or an explicit client are required."
            )
        return client

    async def evaluate(
        self,
        questions_or_state: Any,
        questions: dict[str, BaseQuestion] | None = None,
        *,
        state: str | dict[str, Any] = "",
        model: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Evaluate one or more decision questions with metric collection.

        Supports three invocation patterns:
        1. Single Question:
           `await service.evaluate(question, state=...)` -> returns `BaseAnswer`
        2. Dict of Questions:
           `await service.evaluate({"q1": q1}, state=...)` -> returns `DecisionResult`
        3. Port-style invocation:
           `await service.evaluate(state, questions)` -> returns `DecisionResult`
        """
        client = self._require_client()
        resolved_model = model or self.settings.model

        single_question_mode = False
        target_questions: dict[str, BaseQuestion]
        target_state: str | dict[str, Any]

        if questions is not None:
            # Pattern 3: (state, questions)
            target_state = questions_or_state
            target_questions = questions
        elif isinstance(questions_or_state, BaseQuestion):
            # Pattern 1: (question, state=...)
            single_question_mode = True
            target_state = state
            target_questions = {"decision_q": questions_or_state}
        elif isinstance(questions_or_state, dict):
            # Pattern 2: (questions_dict, state=...)
            target_state = state
            target_questions = questions_or_state
        else:
            raise DecisionClientError(
                f"Invalid evaluate arguments: {type(questions_or_state).__name__}"
            )

        start_time = time.perf_counter()
        try:
            result = await client.evaluate(
                target_state,
                target_questions,
                model=resolved_model,
                **kwargs,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            self._total_evaluations += 1
            self._total_latency_ms += elapsed_ms

            if single_question_mode:
                return result.answers.get("decision_q")
            return result
        except Exception:
            self._total_errors += 1
            raise

    async def evaluate_question(
        self,
        question: BaseQuestion,
        state: str | dict[str, Any] = "",
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> BaseAnswer:
        """Evaluate a single question and return the typed answer."""
        answer = await self.evaluate(question, state=state, model=model, **kwargs)
        if not isinstance(answer, BaseAnswer):
            raise DecisionClientError(f"Expected BaseAnswer, got {type(answer).__name__}")
        return answer

    async def evaluate_choice(
        self,
        state: str | dict[str, Any],
        instructions: str,
        criteria: dict[str, str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChoiceAnswer:
        """Evaluate a categorical choice question."""
        client = self._require_client()
        resolved_model = model or self.settings.model
        start_time = time.perf_counter()
        try:
            answer = await client.evaluate_choice(
                state=state,
                instructions=instructions,
                criteria=criteria,
                model=resolved_model,
                **kwargs,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            self._total_evaluations += 1
            self._total_latency_ms += elapsed_ms
            return answer
        except Exception:
            self._total_errors += 1
            raise

    async def evaluate_noul(
        self,
        state: str | dict[str, Any],
        instructions: str,
        threshold: float = 0.5,
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> NoulAnswer:
        """Evaluate a probabilistic binary question."""
        client = self._require_client()
        resolved_model = model or self.settings.model
        start_time = time.perf_counter()
        try:
            answer = await client.evaluate_noul(
                state=state,
                instructions=instructions,
                threshold=threshold,
                model=resolved_model,
                **kwargs,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            self._total_evaluations += 1
            self._total_latency_ms += elapsed_ms
            return answer
        except Exception:
            self._total_errors += 1
            raise

    async def evaluate_score(
        self,
        state: str | dict[str, Any],
        instructions: str,
        criteria: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> ScoreAnswer:
        """Evaluate an ordered rubric score question."""
        client = self._require_client()
        resolved_model = model or self.settings.model
        start_time = time.perf_counter()
        try:
            answer = await client.evaluate_score(
                state=state,
                instructions=instructions,
                criteria=criteria,
                model=resolved_model,
                **kwargs,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            self._total_evaluations += 1
            self._total_latency_ms += elapsed_ms
            return answer
        except Exception:
            self._total_errors += 1
            raise

    async def close(self) -> None:
        """Release underlying client resources."""
        clients = [*self._retired_clients, self._client]
        self._retired_clients.clear()
        for client in clients:
            if client is not None and hasattr(client, "close"):
                await client.close()

    async def __aenter__(self) -> DecisionService:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.close()
