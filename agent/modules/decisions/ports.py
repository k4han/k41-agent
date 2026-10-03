"""Abstract port definitions for decision clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent.modules.decisions.models import (
    BaseQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionResult,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)


class DecisionClient(ABC):
    """Abstract base class for decision model providers."""

    @abstractmethod
    async def evaluate(
        self,
        state: str | dict[str, Any],
        questions: dict[str, BaseQuestion],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> DecisionResult:
        """Evaluate a state against one or more typed questions.

        Args:
            state: Context or state string/dictionary to be evaluated.
            questions: Dictionary mapping question IDs to typed Question models.
            model: Optional model override.
            **kwargs: Provider-specific options.

        Returns:
            DecisionResult containing typed answers, model info, and telemetry.
        """

    async def evaluate_choice(
        self,
        state: str | dict[str, Any],
        instructions: str,
        criteria: dict[str, str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChoiceAnswer:
        """Evaluate a single categorical choice question.

        Args:
            state: Context or state to evaluate.
            instructions: Task instructions for the decision model.
            criteria: Mapping of candidate options to descriptive criteria.
            model: Optional model override.
            **kwargs: Provider-specific options.

        Returns:
            ChoiceAnswer containing selected choice, probabilities, and confidence.
        """
        result = await self.evaluate(
            state,
            {"choice_q": ChoiceQuestion(instructions=instructions, criteria=criteria)},
            model=model,
            **kwargs,
        )
        answer = result.answers.get("choice_q")
        if not isinstance(answer, ChoiceAnswer):
            raise TypeError(f"Expected ChoiceAnswer, got {type(answer).__name__}")
        return answer

    async def evaluate_noul(
        self,
        state: str | dict[str, Any],
        instructions: str,
        threshold: float = 0.5,
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> NoulAnswer:
        """Evaluate a single binary yes/no question.

        Args:
            state: Context or state to evaluate.
            instructions: Task instructions for the decision model.
            threshold: Probability threshold for true decision.
            model: Optional model override.
            **kwargs: Provider-specific options.

        Returns:
            NoulAnswer containing probability, resolved boolean decision, and confidence.
        """
        result = await self.evaluate(
            state,
            {"noul_q": NoulQuestion(instructions=instructions, threshold=threshold)},
            model=model,
            **kwargs,
        )
        answer = result.answers.get("noul_q")
        if not isinstance(answer, NoulAnswer):
            raise TypeError(f"Expected NoulAnswer, got {type(answer).__name__}")
        return answer

    async def evaluate_score(
        self,
        state: str | dict[str, Any],
        instructions: str,
        criteria: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> ScoreAnswer:
        """Evaluate a single rubric score question.

        Args:
            state: Context or state to evaluate.
            instructions: Task instructions for the decision model.
            criteria: Ordered rubric levels from lowest to highest.
            model: Optional model override.
            **kwargs: Provider-specific options.

        Returns:
            ScoreAnswer containing weighted score, selected level, and confidence.
        """
        result = await self.evaluate(
            state,
            {"score_q": ScoreQuestion(instructions=instructions, criteria=criteria)},
            model=model,
            **kwargs,
        )
        answer = result.answers.get("score_q")
        if not isinstance(answer, ScoreAnswer):
            raise TypeError(f"Expected ScoreAnswer, got {type(answer).__name__}")
        return answer

    async def close(self) -> None:
        """Release underlying HTTP client and connection resources."""

    async def __aenter__(self) -> "DecisionClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.close()
