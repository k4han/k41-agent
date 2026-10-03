"""Domain evaluator for inbound channel message triage."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.modules.decisions.evaluator import BaseDecisionEvaluator
from agent.modules.decisions.models import BaseAnswer, ChoiceAnswer, ChoiceQuestion

DEFAULT_TRIAGE_OPTIONS: dict[str, str] = {
    "respond_default": "General assistant query requiring normal conversational response.",
    "respond_code": "Programming, technical debugging, shell execution, or code review request.",
    "respond_research": "In-depth web search, news investigation, or synthesized report.",
    "ignore_spam": "Automated spam, bot advertisement, promotional noise, or meaningless ping.",
}


class TriageContext(BaseModel):
    """Context input for inbound channel message triage."""

    model_config = ConfigDict(extra="ignore")

    message_text: str
    channel: str = ""
    sender_id: str = ""
    is_group: bool = False
    custom_options: dict[str, str] | None = None


class TriageDecision(BaseModel):
    """Resolved triage action and assessment."""

    model_config = ConfigDict(extra="ignore")

    action: str
    confidence: float
    is_spam: bool
    probabilities: dict[str, float] = Field(default_factory=dict)


class ChannelInboundTriageEvaluator(BaseDecisionEvaluator[TriageContext, TriageDecision]):
    """Categorizes inbound channel messages to filter spam and dispatch workflows efficiently."""

    def build_question(self, context: TriageContext) -> ChoiceQuestion:
        criteria = context.custom_options or DEFAULT_TRIAGE_OPTIONS
        instructions = (
            f"Triage the inbound message from channel '{context.channel or 'chat'}' "
            f"(group_chat={context.is_group}) into the appropriate handling action category.\n"
            f"Message text:\n{context.message_text}"
        )
        return ChoiceQuestion(instructions=instructions, criteria=criteria)

    def build_state(self, context: TriageContext) -> str:
        return (
            f"Channel: {context.channel or 'default'}\n"
            f"Sender: {context.sender_id or 'unknown'}\n"
            f"IsGroup: {context.is_group}\n"
            f"Message: {context.message_text}"
        )

    def parse_result(self, answer: BaseAnswer, context: TriageContext) -> TriageDecision:
        if isinstance(answer, ChoiceAnswer):
            action = answer.choice
            confidence = answer.confidence
            probabilities = answer.probabilities
        else:
            action = getattr(answer, "choice", "respond_default")
            confidence = getattr(answer, "confidence", 0.0)
            probabilities = getattr(answer, "probabilities", {})

        is_spam = action == "ignore_spam" and confidence >= self.confidence_threshold
        return TriageDecision(
            action=action,
            confidence=confidence,
            is_spam=is_spam,
            probabilities=probabilities,
        )
