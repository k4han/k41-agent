"""Translate ACP session updates into stream events and persisted messages.

This module holds no locks and starts no threads; ``AcpTurn`` serializes access.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

# Marks messages already delivered live, so the runner does not re-emit them.
ACP_LIVE_KWARG = "acp_live"
# The runner forwards this custom stream event's payload to consumers unchanged.
ACP_LIVE_EVENT = "acp_live"

MAX_TOOL_TEXT_CHARS = 32_000
MAX_TOOL_INPUT_CHARS = 8_000

TERMINAL_STATUSES = {"completed", "failed"}
# agy_acp_server reports the original call this way once an approved edit ran
# under a second tool call id, and when a turn is cancelled while it was pending.
NEVER_EXECUTED_OUTPUT = "Tool call was approved but never executed."
CANCELLED_NOTICE_TEXT = "The request was cancelled by the client."


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated {len(text) - limit} characters]"


def _clip_value(value: Any) -> Any:
    """Keep small structured input intact and bound large values for checkpoints."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    return value if len(encoded) <= MAX_TOOL_INPUT_CHARS else {"truncated": _clip(encoded, MAX_TOOL_INPUT_CHARS)}


def _content_text(blocks: Any) -> str:
    """Render ACP tool content blocks as text."""
    parts: list[str] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "diff":
            parts.append(f"{'Edited' if block.get('oldText') else 'Wrote'} {block.get('path', '')}")
        elif block.get("type") == "content":
            inner = block.get("content")
            if isinstance(inner, dict) and inner.get("type") == "text":
                parts.append(str(inner.get("text") or ""))
    return "\n".join(part for part in parts if part)


def _output_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("formatted_output", "combinedOutput", "output"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
    if raw is None:
        return ""
    try:
        return json.dumps(raw, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(raw)


@dataclass
class TextItem:
    text: str = ""
    message_id: str | None = None


@dataclass
class ToolItem:
    tool_call_id: str
    title: str = ""
    kind: str = "other"
    status: str = "pending"
    raw_input: Any = None
    raw_output: Any = None
    content: Any = None
    locations: list[Any] = field(default_factory=list)
    announced: bool = False
    finished: bool = False
    dropped: bool = False


class TurnTranscript:
    """Ordered record of one prompt turn."""

    def __init__(self, turn_id: str, thread_id: str = "", provider_label: str = "acp") -> None:
        self.turn_id = turn_id
        self.thread_id = thread_id
        self.provider_label = provider_label
        self.items: list[TextItem | ToolItem] = []
        self._tools: dict[str, ToolItem] = {}
        self.cancel_requested = False

    # Update translation ------------------------------------------------------

    def apply(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        """Merge an update and return the stream events it produces."""
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            return self._apply_text(update)
        if kind == "agent_thought_chunk":
            # Thoughts are live-only; history has no representation for them.
            text = self._chunk_text(update)
            return [{"type": "thinking", "content": text}] if text else []
        if kind in {"tool_call", "tool_call_update"}:
            return self._apply_tool(update)
        if kind == "usage_update":
            return self._apply_usage(update)
        return []

    @staticmethod
    def _chunk_text(update: dict[str, Any]) -> str:
        content = update.get("content")
        return str(content.get("text") or "") if isinstance(content, dict) and content.get("type") == "text" else ""

    def _apply_text(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        text = self._chunk_text(update)
        if not text or (self.cancel_requested and text.strip() == CANCELLED_NOTICE_TEXT):
            return []
        message_id = update.get("messageId")
        last = self.items[-1] if self.items else None
        same_message = isinstance(last, TextItem) and (message_id is None or last.message_id in (None, message_id))
        if same_message:
            last.text += text
        else:
            self.items.append(TextItem(text=text, message_id=message_id))
        return [{"type": "message", "content": text}]

    def tool_item(self, tool_call_id: str) -> ToolItem:
        item = self._tools.get(tool_call_id)
        if item is None:
            item = ToolItem(tool_call_id=tool_call_id)
            self._tools[tool_call_id] = item
            self.items.append(item)
        return item

    def merge_tool(self, item: ToolItem, fields: dict[str, Any]) -> None:
        if fields.get("title"):
            item.title = str(fields["title"])
        if fields.get("kind"):
            item.kind = str(fields["kind"])
        if fields.get("status"):
            item.status = str(fields["status"])
        if fields.get("rawInput") is not None:
            item.raw_input = _clip_value(fields["rawInput"])
        if fields.get("rawOutput") is not None:
            item.raw_output = fields["rawOutput"]
        if fields.get("content") is not None:
            item.content = fields["content"]
        if isinstance(fields.get("locations"), list):
            item.locations = fields["locations"]

    def _apply_tool(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        tool_call_id = str(update.get("toolCallId") or "")
        if not tool_call_id:
            return []
        item = self.tool_item(tool_call_id)
        self.merge_tool(item, update)
        return self._tool_events(item)

    def _tool_events(self, item: ToolItem) -> list[dict[str, Any]]:
        if item.dropped or item.finished:
            return []
        if item.status == "pending":
            # Pending calls wait for input or approval, and tool cards have no
            # update path, so they are announced once they actually start.
            return []
        terminal = item.status in TERMINAL_STATUSES
        if terminal and not item.announced and item.raw_output == NEVER_EXECUTED_OUTPUT:
            item.dropped = True
            return []
        events: list[dict[str, Any]] = []
        if not item.announced:
            item.announced = True
            events.append(self._tool_call_event(item))
        if terminal:
            item.finished = True
            events.append(self._tool_result_event(item))
        return events

    def _apply_usage(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        used, size = update.get("used"), update.get("size")
        if not isinstance(used, int) or not isinstance(size, int) or used < 0 or size <= 0:
            return []
        return [{
            "type": "context_usage",
            "thread_id": self.thread_id,
            "estimated": False,
            "current_context_tokens": used,
            "has_context_usage": True,
            "context_updated_at": datetime.now(UTC).isoformat(),
            "context_window": size,
            "provider": self.provider_label,
        }]

    # Identifiers -------------------------------------------------------------

    def _stable_id(self, label: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.turn_id}:{label}"))

    def public_tool_id(self, item: ToolItem) -> str:
        # Agents can reuse ids across prompts of a session, so ids carry the turn.
        return f"{self.turn_id[:8]}:{item.tool_call_id}"

    @staticmethod
    def tool_name(item: ToolItem) -> str:
        # A prefix keeps agent-chosen names away from names the UI reserves.
        return f"acp_{item.kind or 'other'}"

    # Rendering ---------------------------------------------------------------

    def _tool_args(self, item: ToolItem) -> dict[str, Any]:
        args: dict[str, Any] = {"title": item.title}
        if item.raw_input is not None:
            args["input"] = item.raw_input
        paths = [entry.get("path") for entry in item.locations if isinstance(entry, dict) and entry.get("path")]
        if paths:
            args["paths"] = paths
        return args

    def _tool_call_event(self, item: ToolItem) -> dict[str, Any]:
        return {"type": "tool_call", "id": self.public_tool_id(item), "name": self.tool_name(item), "args": self._tool_args(item)}

    @staticmethod
    def tool_output(item: ToolItem) -> str:
        return _clip(_output_text(item.raw_output) or _content_text(item.content) or item.status, MAX_TOOL_TEXT_CHARS)

    def _tool_result_event(self, item: ToolItem) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_call_id": self.public_tool_id(item),
            "name": self.tool_name(item),
            "content": self.tool_output(item),
        }

    @property
    def final_text(self) -> str:
        for item in reversed(self.items):
            if isinstance(item, TextItem) and item.text.strip():
                return item.text
        return ""

    @property
    def ends_with_text(self) -> bool:
        visible = [item for item in self.items if not (isinstance(item, ToolItem) and not item.announced)]
        return bool(visible) and isinstance(visible[-1], TextItem)

    def messages(self, *, notice: str = "") -> list[BaseMessage]:
        """Render the turn. Stable ids make repeated commits replace in place."""
        output: list[BaseMessage] = []
        flags = {ACP_LIVE_KWARG: True}
        for index, item in enumerate(self.items):
            if isinstance(item, TextItem):
                output.append(AIMessage(content=item.text, id=self._stable_id(f"text:{index}"), additional_kwargs=flags))
                continue
            if item.dropped or not item.announced:
                continue
            public_id = self.public_tool_id(item)
            output.append(AIMessage(
                content="",
                id=self._stable_id(f"call:{item.tool_call_id}"),
                additional_kwargs=flags,
                tool_calls=[{"id": public_id, "name": self.tool_name(item), "args": self._tool_args(item), "type": "tool_call"}],
            ))
            if item.finished:
                output.append(ToolMessage(
                    content=self.tool_output(item),
                    id=self._stable_id(f"result:{item.tool_call_id}"),
                    tool_call_id=public_id,
                    name=self.tool_name(item),
                    status="error" if item.status == "failed" else "success",
                    additional_kwargs=flags,
                ))
        if notice:
            output.append(AIMessage(content=notice, id=self._stable_id("notice"), additional_kwargs=flags))
        return output
