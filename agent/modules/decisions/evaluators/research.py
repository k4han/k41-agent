"""Domain evaluator for research graph stopping criteria."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.modules.decisions.evaluator import BaseDecisionEvaluator
from agent.modules.decisions.models import BaseAnswer, NoulAnswer, NoulQuestion


class ResearchStoppingContext(BaseModel):
    """Context input for evaluating research sufficiency and loop termination."""

    model_config = ConfigDict(extra="ignore")

    query: str
    notes: list[str] = Field(default_factory=list)
    iteration: int = 1
    max_iterations: int = 5


class ResearchStoppingDecision(BaseModel):
    """Resolved research continuation or stopping determination."""

    model_config = ConfigDict(extra="ignore")

    should_stop: bool
    confidence: float
    sufficiency_prob: float
    reason: str = ""


class ResearchStoppingEvaluator(
    BaseDecisionEvaluator[ResearchStoppingContext, ResearchStoppingDecision]
):
    """Determines whether accumulated research findings are sufficient to terminate search loops."""

    def build_question(self, context: ResearchStoppingContext) -> NoulQuestion:
        findings = (
            "\n".join(f"- {note}" for note in context.notes)
            if context.notes
            else "No findings collected yet."
        )
        instructions = (
            f"Has the accumulated research sufficiently and reliably answered the research query?\n"
            f"Query: {context.query}\n"
            f"Findings so far:\n{findings}"
        )
        return NoulQuestion(instructions=instructions, threshold=self.confidence_threshold)

    def build_state(self, context: ResearchStoppingContext) -> str:
        return f"Query: {context.query}\nIteration: {context.iteration} of {context.max_iterations}"

    def parse_result(
        self,
        answer: BaseAnswer,
        context: ResearchStoppingContext,
    ) -> ResearchStoppingDecision:
        if isinstance(answer, NoulAnswer):
            decision = answer.decision
            noul = answer.noul
            confidence = answer.confidence
        else:
            noul = getattr(answer, "noul", 0.0)
            decision = getattr(answer, "decision", False)
            confidence = getattr(answer, "confidence", 0.0)

        if context.iteration >= context.max_iterations:
            return ResearchStoppingDecision(
                should_stop=True,
                confidence=1.0,
                sufficiency_prob=noul,
                reason=f"Max iteration limit ({context.max_iterations}) reached.",
            )

        if decision:
            return ResearchStoppingDecision(
                should_stop=True,
                confidence=confidence,
                sufficiency_prob=noul,
                reason=f"Findings satisfy query requirements (confidence: {confidence:.2f}).",
            )

        return ResearchStoppingDecision(
            should_stop=False,
            confidence=confidence,
            sufficiency_prob=noul,
            reason=f"Additional research required (sufficiency: {noul:.2f} < {self.confidence_threshold:.2f}).",
        )
