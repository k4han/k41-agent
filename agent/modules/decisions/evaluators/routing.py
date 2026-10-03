"""Domain evaluator for agent routing decisions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.modules.decisions.evaluator import BaseDecisionEvaluator
from agent.modules.decisions.models import BaseAnswer, ChoiceAnswer, ChoiceQuestion


class RoutingContext(BaseModel):
    """Context input for agent routing evaluation."""

    model_config = ConfigDict(extra="ignore")

    user_input: str
    candidates: dict[str, str] = Field(
        ...,
        description="Mapping from candidate agent names to descriptive criteria.",
    )
    caller_agent_name: str = ""


class RoutingDecision(BaseModel):
    """Resolved routing decision output."""

    model_config = ConfigDict(extra="ignore")

    selected_agent: str
    confidence: float
    meets_threshold: bool
    probabilities: dict[str, float] = Field(default_factory=dict)


class AgentRoutingEvaluator(BaseDecisionEvaluator[RoutingContext, RoutingDecision]):
    """Evaluates user requests against candidate agent descriptions using ChoiceQuestion."""

    def build_question(self, context: RoutingContext) -> ChoiceQuestion:
        criteria = context.candidates if context.candidates else {"default": "Default general assistant"}
        instructions = (
            f"Select the most appropriate specialist agent to handle the user request.\n"
            f"Caller agent: {context.caller_agent_name or 'user'}\n"
            f"User request: {context.user_input}"
        )
        return ChoiceQuestion(instructions=instructions, criteria=criteria)

    def build_state(self, context: RoutingContext) -> str:
        return f"User Input: {context.user_input}\nCaller: {context.caller_agent_name}"

    def parse_result(self, answer: BaseAnswer, context: RoutingContext) -> RoutingDecision:
        if isinstance(answer, ChoiceAnswer):
            selected = answer.choice
            confidence = answer.confidence
            probabilities = answer.probabilities
        else:
            selected = getattr(answer, "choice", "default")
            confidence = getattr(answer, "confidence", 0.0)
            probabilities = getattr(answer, "probabilities", {})

        meets_threshold = confidence >= self.confidence_threshold
        return RoutingDecision(
            selected_agent=selected,
            confidence=confidence,
            meets_threshold=meets_threshold,
            probabilities=probabilities,
        )
