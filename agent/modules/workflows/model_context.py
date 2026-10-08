"""Prepare model history and graph updates without external checkpoint writes."""

import logging
from typing import Any

from langchain_core.messages import BaseMessage, RemoveMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent.modules.conversations import (
    CompactionSummaryError,
    compact_message_history,
)
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.history_trim import trim_channel_history
from agent.modules.workflows.run_config import DEFAULT_CONTEXT_COMPACT_THRESHOLD
from agent.modules.providers import DEFAULT_CONTEXT_WINDOW

logger = logging.getLogger(__name__)


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
    limit = getattr(resolved, "context_window", DEFAULT_CONTEXT_WINDOW)
    current = history

    def count(items):
        payload = normalize_messages_for_chat_model([system, *items])
        return count_tokens_approximately(payload, tools=tools)

    before = count(current)
    if before * 100 >= limit * threshold:
        try:
            compacted = await compact_message_history(
                current,
                thread_id=str(config.get("configurable", {}).get("thread_id", "")),
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                max_retained_tokens=max(
                    1,
                    limit * threshold // 100 - count_tokens_approximately([system], tools=tools),
                ),
            )
            if count(compacted.messages) < before:
                current = compacted.messages
        except (ValueError, CompactionSummaryError) as exc:
            logger.warning("Automatic context compaction skipped: %s", exc)

    trim_threshold = getattr(context, "channel_context_trim_threshold", None)
    if trim_threshold is not None and not getattr(context, "channel_trim_applied", False):
        current = trim_channel_history(current, trim_threshold)
        context.channel_trim_applied = True

    if current is history:
        return current, []
    return current, [RemoveMessage(id=REMOVE_ALL_MESSAGES), *current]
