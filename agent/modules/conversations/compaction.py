"""Conversation compaction engine leveraging LangChain trim_messages and LLM summarization."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent.modules.conversations.history import (
    _checkpoint_messages,
    get_history_checkpointer,
    get_thread_messages_payload,
)
from agent.modules.conversations.service import (
    _INJECTION_AS_NODE,
    _INJECTION_GRAPH_NAME,
    get_conversation_thread,
)
from agent.modules.providers import get_resolved_chat_model
from agent.modules.usage import (
    attach_usage_context,
    load_usage_context,
    with_usage_tracking,
)
from agent.modules.workflows import get_workflow_graph, make_run_config
from agent.shared.config import get_config_service
from agent.shared.infrastructure.parsing import extract_final_text_content
from agent.shared.thread_ids import resolve_thread_id

logger = logging.getLogger(__name__)

DEFAULT_KEEP_RECENT_MESSAGES = 6
COMPACTION_TIMEOUT_SECONDS = 30.0


class CompactionConflictError(RuntimeError):
    """Raised when new messages arrive during compaction."""


class CompactionSummaryError(RuntimeError):
    """Raised when the LLM summarizer fails and compaction must abort."""


_COMPACTION_LOCKS: dict[str, asyncio.Lock] = {}


def _compaction_lock(thread_id: str) -> asyncio.Lock:
    lock = _COMPACTION_LOCKS.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _COMPACTION_LOCKS[thread_id] = lock
    return lock


def _release_compaction_lock(thread_id: str) -> None:
    """Remove a per-thread lock once it is free to avoid unbounded growth."""
    lock = _COMPACTION_LOCKS.get(thread_id)
    if lock is None:
        return
    try:
        if lock.locked():
            return
        waiters = getattr(lock, "_waiters", None)
        if waiters:
            return
    except Exception:
        return
    _COMPACTION_LOCKS.pop(thread_id, None)


def get_default_keep_recent_messages() -> int:
    """Read configured retention count or default to 6."""
    config = get_config_service()
    return max(2, config.get_int("conversations.compact.keep_recent_messages", DEFAULT_KEEP_RECENT_MESSAGES))


def _format_messages_for_summary(messages: list[BaseMessage]) -> str:
    """Format raw BaseMessages into readable plain-text transcript for the summarizer."""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                texts = [
                    str(p.get("text", ""))
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                text_str = " ".join(texts)
            else:
                text_str = str(content)
            lines.append(f"User: {text_str.strip()}")
        elif isinstance(msg, AIMessage):
            content = extract_final_text_content(getattr(msg, "content", None)) or ""
            tool_calls = getattr(msg, "tool_calls", None)
            tc_str = ""
            if tool_calls and isinstance(tool_calls, list):
                tc_names = [str(tc.get("name", "")) for tc in tool_calls if isinstance(tc, dict)]
                if tc_names:
                    tc_str = f" [Invoked tools: {', '.join(tc_names)}]"
            lines.append(f"Assistant: {content.strip()}{tc_str}")
        elif isinstance(msg, ToolMessage):
            from agent.modules.tools import coding_message_for_model

            msg = coding_message_for_model(msg)
            name = getattr(msg, "name", "tool")
            content = extract_final_text_content(getattr(msg, "content", None)) or ""
            if len(content) > 300:
                content = content[:300] + "... [truncated]"
            lines.append(f"Tool ({name}): {content.strip()}")
        elif isinstance(msg, SystemMessage):
            lines.append(f"System: {str(getattr(msg, 'content', '')).strip()}")
    return "\n\n".join(lines)


async def _generate_context_summary(
    messages: list[BaseMessage],
    *,
    thread_id: str = "",
    provider_name: str | None = None,
    model_name: str | None = None,
    timeout_seconds: float = COMPACTION_TIMEOUT_SECONDS,
) -> str:
    """Generate a dense, factual summary of older messages via the chat model."""
    formatted_history = _format_messages_for_summary(messages)
    system_prompt = (
        "You are an expert context summarizer. Your task is to produce a dense, "
        "concise, and structured summary of the earlier conversation history provided below.\n\n"
        "Extract and organize:\n"
        "1. Core user objectives, preferences, and constraints.\n"
        "2. Key technical decisions made and solutions designed or implemented.\n"
        "3. Files created, read, edited, or referenced.\n"
        "4. Current state and outstanding or pending tasks.\n\n"
        "Do not include conversational filler or pleasantries. Be direct, dense, and factual."
    )
    user_prompt = f"Earlier Conversation History to Summarize:\n\n{formatted_history}"

    resolved = get_resolved_chat_model(
        provider_name=provider_name or None,
        model=model_name or None,
    )
    llm = resolved.model

    prompt_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    config = attach_usage_context(
        {"configurable": {"thread_id": thread_id}, "tags": ["context_compaction"], "metadata": {"context_compaction": True}},
        await load_usage_context(thread_id),
    )
    try:
        response = await asyncio.wait_for(
            llm.ainvoke(
                prompt_messages,
                config=with_usage_tracking(
                    config,
                    agent_name="conversation-compactor",
                    provider_name=resolved.provider_name,
                    model_name=resolved.model_name,
                    call_kind="compaction",
                    internal=True,
                ),
            ),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise CompactionSummaryError(
            f"Failed to generate conversation summary for thread '{thread_id}': {exc}"
        ) from exc
    summary_text = extract_final_text_content(getattr(response, "content", response)) or ""
    if not summary_text.strip():
        raise CompactionSummaryError(
            f"Conversation summarizer returned an empty summary for thread '{thread_id}'."
        )
    return summary_text.strip()


def _build_summary_message_pair(summary_text: str) -> tuple[HumanMessage, AIMessage]:
    """Create a Human/AI message pair representing the compacted context."""
    summary_id = f"compact-{uuid.uuid4().hex[:12]}"
    ack_id = f"compact-ack-{uuid.uuid4().hex[:12]}"
    human_msg = HumanMessage(
        content=(
            "[Conversation Context Summary]\n"
            "The earlier conversation before this point was compacted to conserve context:\n\n"
            f"{summary_text}\n\n"
            "Please acknowledge and continue assisting based on this context."
        ),
        id=summary_id,
        additional_kwargs={"is_compact_summary": True},
    )
    ai_msg = AIMessage(
        content="Understood. I have reviewed the summary of our previous conversation and will continue seamlessly.",
        id=ack_id,
        additional_kwargs={"is_compact_summary": True},
    )
    return human_msg, ai_msg


def is_valid_message_sequence(msgs: list[BaseMessage]) -> bool:
    """Verify that a message sequence is safe for LLM consumption.

    A sequence is valid if:
    1. It is non-empty.
    2. It does not start with an orphan ToolMessage.
    3. Every ToolMessage corresponds to a tool_call_id defined in an earlier AIMessage within this sequence.
    4. All calls receive exactly one result before the next non-tool message.
    """
    if not msgs:
        return False
    if isinstance(msgs[0], ToolMessage):
        return False

    active_tool_call_ids: set[str] = set()
    for m in msgs:
        if isinstance(m, AIMessage):
            if active_tool_call_ids:
                return False
            tool_calls = getattr(m, "tool_calls", None) or []
            for tc in tool_calls:
                if isinstance(tc, dict) and "id" in tc:
                    active_tool_call_ids.add(tc["id"])
                elif hasattr(tc, "id"):
                    active_tool_call_ids.add(getattr(tc, "id"))
        elif isinstance(m, ToolMessage):
            t_id = getattr(m, "tool_call_id", None)
            if not t_id or t_id not in active_tool_call_ids:
                return False
            active_tool_call_ids.remove(t_id)
        elif active_tool_call_ids:
            return False
    return not active_tool_call_ids


def resolve_compaction_cutoff(messages: list[BaseMessage], target_keep: int = DEFAULT_KEEP_RECENT_MESSAGES) -> int:
    """Find the best split index 0 < cutoff < len(messages) ensuring a valid retained sequence.

    Prefers cutting at a HumanMessage turn boundary to preserve full natural turns.
    If no HumanMessage boundary is viable, cuts at the safest AIMessage boundary closest
    to the retention target.
    """
    total = len(messages)
    if total < 3:
        raise ValueError(
            f"Conversation has {total} message(s), which is already concise."
        )

    # 1. Find all safe cutoff indices where the trailing sub-sequence is structurally valid
    safe_indices = [
        i for i in range(1, total)
        if is_valid_message_sequence(messages[i:])
    ]
    if not safe_indices:
        raise ValueError(
            "Unable to compact conversation further while preserving valid message structure."
        )

    # 2. Prefer HumanMessage boundaries
    human_safe = [i for i in safe_indices if isinstance(messages[i], HumanMessage)]
    if human_safe:
        within_target = [i for i in human_safe if total - i <= target_keep]
        if within_target:
            return min(within_target)
        return max(human_safe)

    # 3. If no HumanMessage boundary exists (e.g. 1 user prompt followed by many agent steps),
    # pick the safe boundary closest to the target retention count
    target_i = max(1, total - target_keep)
    return min(safe_indices, key=lambda i: abs(i - target_i))


async def has_pending_interrupt(thread_id: str) -> bool:
    """Check whether a thread is paused on a LangGraph interrupt.

    Uses ``graph.aget_state`` and inspects ``tasks[].interrupts`` plus the
    ``next`` tuple. Returns False on any lookup failure so compaction does
    not get blocked by observability errors; callers should treat True as
    authoritative and False as best-effort.
    """
    thread_id = resolve_thread_id(thread_id)
    try:
        graph = get_workflow_graph(_INJECTION_GRAPH_NAME)
        config = make_run_config(thread_id=thread_id)
        state = await graph.aget_state(config)
    except Exception as exc:
        logger.debug("Failed to check pending interrupt for thread %s: %s", thread_id, exc)
        return False
    try:
        tasks = getattr(state, "tasks", None) or ()
        for task in tasks:
            interrupts = getattr(task, "interrupts", None) or ()
            if interrupts:
                return True
        next_nodes = getattr(state, "next", None) or ()
        if next_nodes:
            return True
    except Exception as exc:
        logger.debug("Failed to parse interrupt state for thread %s: %s", thread_id, exc)
        return False
    return False


@dataclass(frozen=True)
class CompactedHistory:
    messages: list[BaseMessage]
    summary: str
    compacted_count: int
    kept_count: int


async def compact_message_history(
    messages: list[BaseMessage],
    *,
    thread_id: str = "",
    keep_recent_messages: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    max_retained_tokens: int | None = None,
) -> CompactedHistory:
    """Summarize a supplied history without reading or writing graph checkpoints."""
    if len(messages) <= 2:
        raise ValueError("Conversation has too few messages to compact.")
    target_keep = keep_recent_messages if keep_recent_messages is not None else get_default_keep_recent_messages()
    effective_keep = min(target_keep, max(1, len(messages) - 2))

    cutoff_index: int | None = None

    # Try LangChain trim_messages first if a human boundary exists within retention target
    try:
        trimmed = trim_messages(
            messages,
            strategy="last",
            max_tokens=effective_keep,
            token_counter=len,
            start_on="human",
            include_system=False,
            allow_partial=False,
        )
        if trimmed and 0 < len(trimmed) < len(messages) and is_valid_message_sequence(trimmed):
            first_id = getattr(trimmed[0], "id", None)
            if first_id:
                matched_idx = next(
                    (i for i, m in enumerate(messages) if getattr(m, "id", None) == first_id),
                    None,
                )
                if matched_idx is not None and 0 < matched_idx < len(messages):
                    cutoff_index = matched_idx
    except Exception:
        cutoff_index = None

    # Fall back to robust turn & tool-chain resolution
    if cutoff_index is None:
        cutoff_index = resolve_compaction_cutoff(messages, target_keep=effective_keep)

    if max_retained_tokens is not None:
        from langchain_core.messages.utils import count_tokens_approximately
        from agent.modules.workflows import normalize_messages_for_chat_model

        def retained_tokens(index: int) -> int:
            return count_tokens_approximately(normalize_messages_for_chat_model(messages[index:]))

        # A long active turn can exceed the budget by itself. Cut between complete
        # tool groups when keeping the entire turn would prevent useful compaction.
        if retained_tokens(cutoff_index) >= max_retained_tokens:
            for index in range(cutoff_index + 1, len(messages)):
                if is_valid_message_sequence(messages[index:]) and retained_tokens(index) < max_retained_tokens:
                    cutoff_index = index
                    break

    to_summarize = messages[:cutoff_index]
    recent_messages = messages[cutoff_index:]

    summary_text = await _generate_context_summary(
        to_summarize,
        thread_id=thread_id,
        provider_name=provider_name,
        model_name=model_name,
    )
    summary_human, summary_ai = _build_summary_message_pair(summary_text)

    # Avoid consecutive assistant messages when recent window begins with an AIMessage
    if recent_messages and isinstance(recent_messages[0], AIMessage):
        injected_messages = [
            summary_human,
            *recent_messages,
        ]
    else:
        injected_messages = [
            summary_human,
            summary_ai,
            *recent_messages,
        ]

    return CompactedHistory(injected_messages, summary_text, len(to_summarize), len(recent_messages))


async def compact_conversation_thread(
    thread_id: str,
    *,
    keep_recent_messages: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Compact older messages of a thread into a summary, preserving the latest turns.

    Uses LangChain's trim_messages and robust turn-boundary resolution to safely resolve
    recent messages without cutting in the middle of tool-call/result chains, then replaces
    earlier messages in the LangGraph checkpoint state.
    """
    thread_id = resolve_thread_id(thread_id)
    if not thread_id:
        raise ValueError("thread_id is required.")

    lock = _compaction_lock(thread_id)
    async with lock:
        try:
            return await _compact_conversation_thread_locked(
                thread_id,
                keep_recent_messages=keep_recent_messages,
                provider_name=provider_name,
                model_name=model_name,
            )
        finally:
            _release_compaction_lock(thread_id)


async def _compact_conversation_thread_locked(
    thread_id: str,
    *,
    keep_recent_messages: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Inner compaction implementation, called with per-thread lock held."""
    thread_id = resolve_thread_id(thread_id)
    target_keep = (
        keep_recent_messages
        if keep_recent_messages is not None and keep_recent_messages >= 2
        else get_default_keep_recent_messages()
    )

    checkpointer = get_history_checkpointer()
    config = make_run_config(thread_id=thread_id)
    checkpoint_tuple = await checkpointer.aget_tuple(config)
    if checkpoint_tuple is None:
        raise LookupError(f"No checkpoint found for thread '{thread_id}'.")

    messages = _checkpoint_messages(checkpoint_tuple)
    if len(messages) <= 2:
        raise ValueError(
            f"Conversation has {len(messages)} message(s), which is already concise "
            f"(retention threshold is {target_keep})."
        )

    if await has_pending_interrupt(thread_id):
        raise CompactionConflictError(
            "Cannot compact while conversation is awaiting user input. "
            "Please answer or dismiss the pending request first."
        )

    if not provider_name or not model_name:
        try:
            thread_meta = await get_conversation_thread(thread_id)
            if thread_meta:
                provider_name = provider_name or thread_meta.get("provider")
                model_name = model_name or thread_meta.get("model")
        except Exception:
            logger.debug("Could not read thread metadata for %s; using default model.", thread_id)

    compacted = await compact_message_history(
        messages,
        thread_id=thread_id,
        keep_recent_messages=target_keep,
        provider_name=provider_name,
        model_name=model_name,
    )
    injected_messages = compacted.messages

    # Re-read checkpoint after the (slow) LLM summarize call. If new messages
    # arrived meanwhile, abort instead of wiping them with REMOVE_ALL.
    fresh_tuple = await checkpointer.aget_tuple(config)
    if fresh_tuple is not None:
        fresh_messages = _checkpoint_messages(fresh_tuple)
        fresh_ids = [getattr(m, "id", None) for m in fresh_messages]
        orig_ids = [getattr(m, "id", None) for m in messages]
        if len(fresh_messages) != len(messages) or fresh_ids != orig_ids:
            raise CompactionConflictError(
                "Conversation changed during compaction. Please try again."
            )

    if await has_pending_interrupt(thread_id):
        raise CompactionConflictError(
            "Cannot compact while conversation is awaiting user input. "
            "Please answer or dismiss the pending request first."
        )

    graph = get_workflow_graph(_INJECTION_GRAPH_NAME)
    await graph.aupdate_state(
        config,
        {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *injected_messages,
            ]
        },
        as_node=_INJECTION_AS_NODE,
    )

    updated_messages, active_checkpoint_id = await get_thread_messages_payload(
        thread_id,
        include_branch_metadata=True,
    )

    from langchain_core.messages.utils import count_tokens_approximately
    retained_tokens = count_tokens_approximately(injected_messages)

    try:
        from agent.modules.usage import UsageEventInput, get_usage_service

        identity = await load_usage_context(thread_id)
        usage_event = UsageEventInput(
            thread_id=thread_id,
            root_thread_id=thread_id,
            platform=identity.platform,
            user_id=identity.user_id,
            channel_id=identity.channel_id,
            agent_name="conversation-compactor",
            provider_name=provider_name or "",
            model_name=model_name or "",
            call_kind="compaction",
            internal=True,
            has_usage_metadata=False,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            usage_metadata={"retained_tokens": retained_tokens},
        )
        await get_usage_service().record_event(usage_event)
    except Exception as exc:
        logger.debug("Failed to record compaction usage event for thread %s: %s", thread_id, exc)

    return {
        "status": "compacted",
        "thread_id": thread_id,
        "active_checkpoint_id": active_checkpoint_id,
        "messages": updated_messages,
        "compacted_count": compacted.compacted_count,
        "kept_count": compacted.kept_count,
        "summary": compacted.summary,
        "retained_tokens": retained_tokens,
        "current_context_tokens": retained_tokens,
    }


__all__ = [
    "DEFAULT_KEEP_RECENT_MESSAGES",
    "CompactionConflictError",
    "CompactionSummaryError",
    "compact_conversation_thread",
    "get_default_keep_recent_messages",
    "has_pending_interrupt",
    "is_valid_message_sequence",
    "resolve_compaction_cutoff",
]
