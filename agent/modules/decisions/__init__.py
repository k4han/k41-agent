"""Public package interface for the decisions module.

Other modules must import decision components from this package root,
not from internal implementation files.
"""

from __future__ import annotations

from agent.modules.decisions.cloudflare import CloudflareClefClient
from agent.modules.decisions.evaluator import BaseDecisionEvaluator
from agent.modules.decisions.evaluators import (
    AgentRoutingEvaluator,
    ChannelInboundTriageEvaluator,
    ResearchStoppingContext,
    ResearchStoppingDecision,
    ResearchStoppingEvaluator,
    RoutingContext,
    RoutingDecision,
    SafetyCheckContext,
    SafetyCheckDecision,
    SafetyCheckEvaluator,
    ToolFilterContext,
    ToolFilterDecision,
    ToolPreFilterEvaluator,
    TriageContext,
    TriageDecision,
)
from agent.modules.decisions.mock import MockDecisionClient
from agent.modules.decisions.models import (
    AnswerUnion,
    BaseAnswer,
    BaseQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionAPIError,
    DecisionAuthenticationError,
    DecisionClientError,
    DecisionError,
    DecisionRateLimitError,
    DecisionResult,
    DecisionTimeoutError,
    NoulAnswer,
    NoulQuestion,
    QuestionUnion,
    ScoreAnswer,
    ScoreQuestion,
)
from agent.modules.decisions.ports import DecisionClient
from agent.modules.decisions.service import DecisionService
from agent.modules.decisions.settings import (
    DecisionSettings,
    RouterMode,
    load_decision_settings,
)

__all__ = [
    # Port, Clients & Service
    "DecisionClient",
    "CloudflareClefClient",
    "MockDecisionClient",
    "DecisionService",
    # Settings
    "DecisionSettings",
    "RouterMode",
    "load_decision_settings",
    # Evaluator Base & Domain Evaluators
    "BaseDecisionEvaluator",
    "AgentRoutingEvaluator",
    "RoutingContext",
    "RoutingDecision",
    "ChannelInboundTriageEvaluator",
    "TriageContext",
    "TriageDecision",
    "ToolPreFilterEvaluator",
    "ToolFilterContext",
    "ToolFilterDecision",
    "ResearchStoppingEvaluator",
    "ResearchStoppingContext",
    "ResearchStoppingDecision",
    "SafetyCheckEvaluator",
    "SafetyCheckContext",
    "SafetyCheckDecision",
    # Question Models
    "BaseQuestion",
    "ChoiceQuestion",
    "NoulQuestion",
    "ScoreQuestion",
    "QuestionUnion",
    # Answer Models
    "BaseAnswer",
    "ChoiceAnswer",
    "NoulAnswer",
    "ScoreAnswer",
    "AnswerUnion",
    # Result Model
    "DecisionResult",
    # Exceptions
    "DecisionError",
    "DecisionClientError",
    "DecisionAPIError",
    "DecisionAuthenticationError",
    "DecisionRateLimitError",
    "DecisionTimeoutError",
]
