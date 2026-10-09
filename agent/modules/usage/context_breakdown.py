"""Estimate the composition of the actual input to a model call."""

from collections.abc import Sequence
from math import floor

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

CONTEXT_CATEGORIES = (
    "system_prompt", "system_tools", "skills", "subagents",
    "user_messages", "agent_responses", "tool_calls",
)


def estimate_context_breakdown(
    messages: Sequence[BaseMessage],
    *,
    tools: list | None = None,
    prompt_sections: dict[str, str] | None = None,
) -> dict[str, int]:
    """Count prompt sections, bound tool schemas and retained message history."""
    result = dict.fromkeys(CONTEXT_CATEGORIES, 0)
    sections = dict(prompt_sections or {})
    for message in messages:
        if isinstance(message, SystemMessage):
            content = message.content
            if isinstance(content, str):
                for category, section_key in (("skills", "skills"), ("skills", "active_skills"), ("subagents", "subagents")):
                    section = sections.pop(section_key, "")
                    if not section:
                        continue
                    result[category] += count_tokens_approximately(
                        [SystemMessage(content=section)], extra_tokens_per_message=0,
                    )
                    if section in content:
                        content = content.replace(section, "", 1)
                message = message.model_copy(update={"content": content})
            result["system_prompt"] += count_tokens_approximately([message])
        elif isinstance(message, HumanMessage):
            result["user_messages"] += count_tokens_approximately([message])
        elif isinstance(message, ToolMessage):
            result["tool_calls"] += count_tokens_approximately([message])
        elif isinstance(message, AIMessage):
            without_calls = message.model_copy(update={"tool_calls": [], "additional_kwargs": {
                key: value for key, value in message.additional_kwargs.items()
                if key not in {"tool_calls", "function_call"}
            }})
            response_tokens = count_tokens_approximately([without_calls])
            result["agent_responses"] += response_tokens
            result["tool_calls"] += max(0, count_tokens_approximately([message]) - response_tokens)
        else:
            result["system_prompt"] += count_tokens_approximately([message])
    result["system_tools"] = count_tokens_approximately([], tools=tools) if tools else 0
    return result


def reconcile_context_breakdown(estimates: dict[str, int], input_tokens: int) -> dict[str, int]:
    """Scale estimates to provider usage, preserving the total after rounding."""
    weights = {key: max(0, estimates.get(key, 0)) for key in CONTEXT_CATEGORIES}
    total = sum(weights.values())
    if not total:
        return {**weights, "system_prompt": input_tokens}
    scaled = {key: value * input_tokens / total for key, value in weights.items()}
    result = {key: floor(value) for key, value in scaled.items()}
    remainder = input_tokens - sum(result.values())
    for key in sorted(scaled, key=lambda key: scaled[key] - result[key], reverse=True)[:remainder]:
        result[key] += 1
    return result


def estimate_compacted_context_breakdown(
    messages: Sequence[BaseMessage],
    previous_breakdown: dict[str, int] | None = None,
) -> dict[str, int]:
    """Recount retained history and carry forward the latest prompt estimates."""
    from agent.modules.workflows import normalize_messages_for_chat_model

    result = estimate_context_breakdown(normalize_messages_for_chat_model(list(messages)))
    for key in ("system_prompt", "system_tools", "skills", "subagents"):
        value = (previous_breakdown or {}).get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = max(result[key], value)
    return result


def estimate_response_breakdown(responses: Sequence[BaseMessage], output_tokens: int) -> dict[str, int]:
    """Split reported output tokens between assistant content and tool calls."""
    estimates = estimate_context_breakdown(responses)
    weights = dict.fromkeys(CONTEXT_CATEGORIES, 0)
    for key in ("agent_responses", "tool_calls"):
        weights[key] = estimates[key]
    if not sum(weights.values()):
        weights["agent_responses"] = 1
    return reconcile_context_breakdown(weights, output_tokens)


def include_response_in_context(
    input_breakdown: dict[str, int],
    output_tokens: int,
    response_breakdown: dict[str, int] | None = None,
) -> dict[str, int]:
    """Add the latest response once to the retained input context."""
    output_breakdown = response_breakdown or estimate_response_breakdown([], output_tokens)
    return {
        key: input_breakdown.get(key, 0) + output_breakdown.get(key, 0)
        for key in CONTEXT_CATEGORIES
    }
