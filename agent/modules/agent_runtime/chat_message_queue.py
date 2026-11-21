"""Process-local queue for dashboard chat turns and model input."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.config import get_stream_writer

from agent.modules.agent_runtime.input_messages import DEFAULT_ATTACHMENT_PROMPT
from agent.modules.workflows import REACT_AGENT_GRAPH_TYPE

logger = logging.getLogger(__name__)

INSERTABLE_WORKFLOWS = {REACT_AGENT_GRAPH_TYPE, "decision_agent", "research_chain"}


class ChatQueueConflict(ValueError):
    """The queue or message moved on before the action could be applied."""

    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id


@dataclass
class QueuedChatMessage:
    id: str
    text: str
    attachments: list[dict[str, Any]]
    revision: int = 0
    priority: bool = False
    sending: bool = False

    @property
    def message_id(self) -> str:
        return f"user-queued-{self.id}"

    def update_text(self, text: str) -> None:
        if not text and not self.attachments:
            raise ValueError("Enter a message or attach a file.")
        self.text = text

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "text": self.text,
            "attachments": [
                {key: attachment[key] for key in ("name", "kind", "mime_type", "size")}
                for attachment in self.attachments
            ],
            "priority": self.priority,
            "sending": self.sending,
        }


class ChatMessageQueue:
    """All transitions run synchronously on the chat stream manager's event loop."""

    def __init__(self, thread_id: str, owner: str) -> None:
        self.thread_id = thread_id
        self.owner = owner
        self.revision = 0
        self.run_id: str | None = None
        self.last_run_id: str | None = None
        self.paused = False
        self.pause_version = 0
        self.waiting = False
        self.can_insert = False
        self.run_options: dict[str, Any] | None = None
        self.messages: dict[str, QueuedChatMessage] = {}
        self.accepted_ids: set[str] = set()
        self._emit: Callable[[str, dict[str, Any]], None] | None = None

    def snapshot(self) -> dict[str, Any]:
        status = "idle"
        if self.paused:
            status = "paused"
        elif self.run_id:
            status = "running"
        elif self.waiting:
            status = "waiting"
        return {
            "thread_id": self.thread_id,
            "revision": self.revision,
            "run_id": self.run_id,
            "last_run_id": self.last_run_id,
            "status": status,
            "can_insert": self.can_insert,
            "messages": [entry.snapshot() for entry in self.messages.values()],
        }

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._emit is not None:
            self._emit(event_type, payload)

    def changed(self) -> None:
        self.revision += 1
        self.emit("chat_queue", self.snapshot())

    def begin(self, run_id: str, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self.run_id = self.last_run_id = run_id
        self.waiting = False
        self.can_insert = False
        self._emit = emit
        self.changed()

    def finish(self, run_id: str, outcome: str) -> None:
        if self.run_id != run_id:
            return
        self.run_id = None
        self.can_insert = False
        self.waiting = outcome == "interrupted" and not self.paused
        self.paused = self.paused or outcome == "failed"
        self.changed()
        self._emit = None

    def pause(self) -> None:
        # A second stop can arrive while a paused queue is preparing to restart.
        self.pause_version += 1
        if not self.paused:
            self.paused = True
            self.waiting = False
            self.changed()

    def set_workflow(self, workflow: str) -> None:
        can_insert = workflow in INSERTABLE_WORKFLOWS
        if self.can_insert != can_insert:
            self.can_insert = can_insert
            self.changed()

    def enqueue(self, run_id: str, message: QueuedChatMessage) -> None:
        if message.id in self.accepted_ids:
            return
        if not run_id or run_id != self.last_run_id:
            raise ChatQueueConflict("The active chat run changed. Refresh the queue and try again.")
        message.update_text(message.text)
        self.accepted_ids.add(message.id)
        self.messages[message.id] = message
        self.changed()

    def editable(self, message_id: str, revision: int) -> QueuedChatMessage:
        message = self.messages.get(message_id)
        if message is None or revision != message.revision or message.sending:
            raise ChatQueueConflict("The queued message changed or is already being sent.")
        return message

    def next_message(self) -> QueuedChatMessage | None:
        if self.paused or self.waiting:
            return None
        pending = [entry for entry in self.messages.values() if not entry.sending]
        return next((entry for entry in pending if entry.priority), pending[0] if pending else None)


class ChatQueueTurn:
    """Track which queued inputs belong to a graph invocation until they checkpoint."""

    def __init__(self, queue: ChatMessageQueue, message: QueuedChatMessage | None) -> None:
        self.queue = queue
        self.message = message
        self.failed = False
        self._claims: dict[str, QueuedChatMessage] = {}
        self._announced: set[str] = set()
        self._observed: set[str] = set()
        self._graph: Any = None
        self._checkpoint_config: dict[str, Any] = {}
        if message is not None:
            self._claim([message])

    def bind(self, graph, context, workflow: str, workspace, usage_context: dict[str, Any]) -> None:
        self._graph = graph
        self._checkpoint_config = {"configurable": {"thread_id": self.queue.thread_id}}
        context.chat_queue_turn = self
        self.queue.set_workflow(workflow)
        self.queue.run_options = {
            "thread_id": self.queue.thread_id,
            "agent_name": context.agent_name,
            "workflow": workflow,
            "workspace": workspace or context.get_workspace(),
            "context_compact_threshold": context.context_compact_threshold,
            "channel_context_trim_threshold": context.channel_context_trim_threshold,
            "allowed_tool_names": context.allowed_tool_names,
            "allowed_skill_names": context.allowed_skill_names,
            "provider": context.provider,
            "model": context.model,
            "reasoning_effort": context.reasoning_effort,
            "permission_mode": context.permission_mode,
            "browser_owner_id": context.browser_owner_id,
            "acp_config_options": context.acp_config_options,
            "usage_context": usage_context,
        }

    def _claim(self, entries: list[QueuedChatMessage]) -> None:
        for entry in entries:
            entry.sending = True
            self._claims[entry.message_id] = entry
        self.queue.changed()

    @staticmethod
    def _delivery(entry: QueuedChatMessage, *, inserted: bool) -> dict[str, Any]:
        return {
            "type": "chat_queue_delivery",
            "message_id": entry.message_id,
            "text": entry.text or DEFAULT_ATTACHMENT_PROMPT,
            "attachments": entry.snapshot()["attachments"],
            "inserted": inserted,
        }

    def announce_turn_input(self) -> dict[str, Any]:
        entry = self.message
        assert entry is not None
        self._announced.add(entry.message_id)
        return self._delivery(entry, inserted=False)

    async def take_insertions(self, thread_id: str, workspace) -> list[HumanMessage]:
        queue = self.queue
        if thread_id != queue.thread_id or queue.paused or not queue.can_insert:
            return []
        pending = [entry for entry in queue.messages.values() if entry.priority and not entry.sending]
        if not pending:
            return []

        from agent.modules.agent_runtime.runner import _ingest_attachments_to_sandbox, _make_user_message

        self._claim(pending)
        messages = []
        writer = get_stream_writer()
        for entry in pending:
            if entry.attachments:
                await _ingest_attachments_to_sandbox(
                    entry.attachments, workspace, thread_id=thread_id, message_id=entry.message_id,
                )
            message = await asyncio.to_thread(
                _make_user_message, entry.text, entry.attachments, workspace, message_id=entry.message_id,
            )
            self._announced.add(entry.message_id)
            writer({**self._delivery(entry, inserted=True), "thread_id": thread_id})
            messages.append(message)
        return messages

    def observe(self, messages: Iterable[BaseMessage]) -> None:
        if self._claims:
            self._observed.update(message.id for message in messages if message.id in self._claims)

    async def settle(self) -> None:
        if not self._claims:
            return
        committed: set[str] = set()
        try:
            if getattr(self._graph, "checkpointer", None):
                latest = await self._graph.aget_state(self._checkpoint_config)
                committed = self._claims.keys() & {message.id for message in latest.values.get("messages", [])}
                missing = self._observed - committed
                if missing:
                    # A later model step can compact a message that was saved earlier in this turn.
                    async for state in self._graph.aget_state_history(self._checkpoint_config):
                        committed.update(missing & {message.id for message in state.values.get("messages", [])})
                        if missing <= committed:
                            break
        except Exception:
            self.failed = True
            logger.warning("Could not confirm queued chat inputs for %s", self.queue.thread_id, exc_info=True)
        finally:
            for message_id, entry in self._claims.items():
                if message_id in committed:
                    self.queue.messages.pop(entry.id, None)
                else:
                    entry.sending = False
                    entry.revision += 1
            reverted = sorted(self._announced - committed)
            if reverted:
                self.queue.emit("chat_queue_reverted", {"message_ids": reverted})
            self.queue.changed()
            self._claims.clear()
            if reverted:
                self.failed = True
