"""Domain evaluator for tool pre-filtering and pruning."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.modules.decisions.evaluator import BaseDecisionEvaluator
from agent.modules.decisions.models import BaseAnswer, ChoiceAnswer, ChoiceQuestion

DEFAULT_TOOL_CATEGORIES: dict[str, str] = {
    "filesystem": "File read, write, edit, directory listing, and patch operations.",
    "web": "Internet web search, URL reading, and external API fetching.",
    "git": "Git version control operations, diff inspection, branches, and commits.",
    "bash": "Terminal commands, shell execution, and system process inspection.",
    "general": "General conversation, internal calculations, or task coordination.",
}


class ToolFilterContext(BaseModel):
    """Context input for tool capability pre-filtering."""

    model_config = ConfigDict(extra="ignore")

    user_query: str
    available_tools: list[str] = Field(default_factory=list)
    categories: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_TOOL_CATEGORIES))


class ToolFilterDecision(BaseModel):
    """Resolved tool capability pre-filtering decision."""

    model_config = ConfigDict(extra="ignore")

    selected_category: str
    confidence: float
    probabilities: dict[str, float] = Field(default_factory=dict)
    relevant_categories: list[str] = Field(default_factory=list)


class ToolPreFilterEvaluator(BaseDecisionEvaluator[ToolFilterContext, ToolFilterDecision]):
    """Selects relevant tool domain capabilities to prune prompt token usage."""

    def build_question(self, context: ToolFilterContext) -> ChoiceQuestion:
        instructions = (
            f"Determine the primary tool capability domain required to fulfill the user request.\n"
            f"User Query:\n{context.user_query}"
        )
        return ChoiceQuestion(instructions=instructions, criteria=context.categories)

    def build_state(self, context: ToolFilterContext) -> str:
        tools_str = ", ".join(context.available_tools) if context.available_tools else "none"
        return f"User Query: {context.user_query}\nAvailable Tools: {tools_str}"

    def parse_result(self, answer: BaseAnswer, context: ToolFilterContext) -> ToolFilterDecision:
        if isinstance(answer, ChoiceAnswer):
            selected = answer.choice
            confidence = answer.confidence
            probabilities = answer.probabilities
        else:
            selected = getattr(answer, "choice", "general")
            confidence = getattr(answer, "confidence", 0.0)
            probabilities = getattr(answer, "probabilities", {})

        # Retain all categories with meaningful probability mass (>= 20%)
        relevant = [cat for cat, p in probabilities.items() if p >= 0.20]
        if not relevant and selected:
            relevant = [selected]

        return ToolFilterDecision(
            selected_category=selected,
            confidence=confidence,
            probabilities=probabilities,
            relevant_categories=relevant,
        )
