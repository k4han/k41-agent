"""Base abstract evaluator for domain-specific decision tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from agent.modules.decisions.models import BaseAnswer, BaseQuestion
from agent.modules.decisions.service import DecisionService

# Aliases for domain evaluators
BaseDecisionQuestion = BaseQuestion
BaseDecisionAnswer = BaseAnswer

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


class BaseDecisionEvaluator(Generic[TInput, TOutput], ABC):
    """Generic base class for domain-specific decision evaluators."""

    def __init__(
        self,
        service: DecisionService,
        confidence_threshold: float = 0.75,
    ) -> None:
        self.service = service
        self.confidence_threshold = confidence_threshold

    @abstractmethod
    def build_question(self, context: TInput) -> BaseQuestion:
        """Map domain context into a typed decision question."""
        ...

    def build_state(self, context: TInput) -> str | dict[str, Any]:
        """Optional state or context representation for the decision engine."""
        return ""

    @abstractmethod
    def parse_result(self, answer: BaseAnswer, context: TInput) -> TOutput:
        """Map typed decision answer back to domain output model."""
        ...

    async def evaluate(self, context: TInput) -> TOutput:
        """Execute decision evaluation lifecycle for the given context."""
        question = self.build_question(context)
        state = self.build_state(context)
        answer = await self.service.evaluate(question, state=state)
        return self.parse_result(answer, context)
