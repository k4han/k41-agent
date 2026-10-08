"""Resolve per-run reasoning settings from agent cards and runtime overrides."""

from typing import TYPE_CHECKING, Any

from agent.modules.providers import get_chat_model_selection, get_reasoning_effort_kwargs

if TYPE_CHECKING:
    from agent.modules.agents.models import AgentConfig
    from agent.modules.providers.models import ResolvedChatModel
    from agent.modules.workflows.run_config import WorkflowContext


def get_workflow_reasoning_effort_kwargs(
    context: "WorkflowContext | None", agent_config: "AgentConfig", resolved: "ResolvedChatModel",
) -> dict[str, Any]:
    """Apply card effort only to its model and reset overrides on fallback."""
    card_effort = getattr(agent_config, "reasoning_effort", None)
    effort = getattr(context, "reasoning_effort", None)
    if getattr(resolved, "used_fallback", False):
        effort = None
    elif effort == "auto":
        # Explicit Auto bypasses the card default and uses the resolved model default.
        effort = None
    elif effort is None and card_effort is not None:
        try:
            card_selection = get_chat_model_selection(
                provider_name=agent_config.provider, model=agent_config.model,
            )
        except (LookupError, ValueError, RuntimeError):
            # An unavailable card default must not affect a valid runtime override.
            card_selection = None
        if card_selection == (resolved.provider_name, resolved.model_name):
            effort = card_effort
    return get_reasoning_effort_kwargs(
        resolved.provider_type, resolved.model_name, effort,
        profile=getattr(resolved, "profile", None),
    )
