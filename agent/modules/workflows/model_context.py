"""Prepare model history and graph updates without external checkpoint writes."""

import logging
import math
import operator
from typing import Any

from langchain_core.messages import BaseMessage, RemoveMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.config import get_stream_writer
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent.modules.conversations import (
    CompactionBudgetError,
    CompactionSummaryError,
    compact_message_history,
)
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.history_trim import trim_channel_history
from agent.modules.workflows.run_config import DEFAULT_CONTEXT_COMPACT_THRESHOLD
from agent.modules.providers import DEFAULT_CONTEXT_WINDOW, model_input_budget
from agent.modules.usage import (
    estimate_response_breakdown,
    include_response_in_context,
    reconcile_context_breakdown,
)

logger = logging.getLogger(__name__)


def _coerce_input_tokens(value: Any) -> int | None:
    """Normalize provider token counts to non-negative ints.

    Providers usually return int, but integer-valued floats, IntEnum and
    numpy integers appear in the wild. Bool is explicitly rejected.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or value < 0:
            return None
        return int(value)
    try:
        coerced = operator.index(value)
    except TypeError:
        return None
    return coerced if coerced >= 0 else None


def emit_reported_context_usage(
    response: BaseMessage, *, resolved: Any, config: dict,
    context_breakdown: dict[str, int] | None = None,
) -> None:
    """Publish retained input and response tokens after a model call."""
    usage = getattr(response, "usage_metadata", None) or {}
    tokens = _coerce_input_tokens(usage.get("input_tokens"))
    if tokens is None:
        logger.debug(
            "Dropping context_usage event with invalid input_tokens=%r", usage.get("input_tokens")
        )
        return
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    output_tokens = _coerce_input_tokens(usage.get("output_tokens")) or 0
    event = {
        "type": "context_usage",
        "thread_id": str(config.get("configurable", {}).get("thread_id", "")),
        "current_context_tokens": tokens + output_tokens,
        "input_tokens": tokens,
        "output_tokens": output_tokens,
        "context_window": getattr(resolved, "context_window", DEFAULT_CONTEXT_WINDOW),
        "provider": resolved.provider_name,
        "model": resolved.model_name,
        "estimated": False,
    }
    if context_breakdown is not None:
        event["context_breakdown"] = include_response_in_context(
            reconcile_context_breakdown(context_breakdown, tokens), output_tokens,
            estimate_response_breakdown([response], output_tokens),
        )
    writer(event)


async def prepare_model_context(
    history: list[BaseMessage],
    *,
    system: SystemMessage,
    context: Any,
    agent_config: Any,
    resolved: Any,
    config: dict,
    tools: list | None = None,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Compact before every model call and trim channel history once per run."""
    override = getattr(context, "context_compact_threshold", None)
    threshold = override if override is not None else getattr(agent_config, "context_compact_threshold", DEFAULT_CONTEXT_COMPACT_THRESHOLD)
    target_budget = model_input_budget(resolved, threshold)
    hard_budget = model_input_budget(resolved)
    current = history

    def count(items):
        payload = normalize_messages_for_chat_model([system, *items])
        return count_tokens_approximately(payload, tools=tools)

    before = count(current)
    if before >= target_budget:
        try:
            compacted = await compact_message_history(
                current,
                thread_id=str(config.get("configurable", {}).get("thread_id", "")),
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                max_retained_tokens=target_budget - count([]),
            )
            after = count(compacted.messages)
            if after < before and after <= target_budget:
                current = compacted.messages
        except (ValueError, CompactionSummaryError) as exc:
            logger.warning("Automatic context compaction skipped: %s", exc)

    trim_threshold = getattr(context, "channel_context_trim_threshold", None)
    if trim_threshold is not None and not getattr(context, "channel_trim_applied", False):
        current = trim_channel_history(current, trim_threshold)
        context.channel_trim_applied = True

    if count(current) > hard_budget:
        raise CompactionBudgetError(
            "Conversation exceeds the model input budget after compaction. "
            "Reduce the latest message or tool output, unload instructions, or select a larger context model."
        )

    if current is history:
        return current, []
    return current, [RemoveMessage(id=REMOVE_ALL_MESSAGES), *current]
