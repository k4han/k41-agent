"""One ACP prompt turn as a thread-safe state machine.

Turn state is fed by the connection's reader thread and consumed by graph nodes
on the event loop. A LangGraph interrupt ends the node but must not end the turn,
because the agent is still waiting for its permission answer, so nothing here is
tied to a node's lifetime. Only nodes write to the stream, since LangGraph's stream
writer needs node context.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage

from agent.modules.acp.client import CANCELLED_OUTCOME, AcpClient
from agent.modules.acp.errors import AcpConnectionClosed, AcpRpcError
from agent.modules.acp.tool_bridge import bridged_tool_name
from agent.modules.acp.translator import TurnTranscript, _clip_value, mcp_call_arguments

if TYPE_CHECKING:
    from agent.modules.acp.tool_bridge import SessionTools

logger = logging.getLogger(__name__)

_ALLOW_KINDS = ("allow_once",)
_REJECT_KINDS = ("reject_once", "reject_always")
GRANT_LABEL = "Allow in this conversation"

STOP_TURN_OPTION_ID = "__stop_turn__"
PERMISSION_INTERRUPT_TYPE = "acp_permission_request"
PERMISSION_QUESTION_PREFIX = "acp_permission:"

_NORMAL_STOPS = {"end_turn", "cancelled"}
_STOP_NOTICES = {
    "max_tokens": "The agent stopped because it reached its token limit.",
    "max_turn_requests": "The agent stopped because it reached its request limit for one turn.",
    "refusal": "The agent declined to continue.",
}
SIGN_IN_NOTICE = "This agent is not signed in. Sign in under Settings > ACP agents, then send your message again."
NO_FINAL_TEXT_NOTICE = "The agent finished without a final message."


def describe_failure(error: BaseException) -> str:
    if isinstance(error, AcpRpcError):
        return SIGN_IN_NOTICE if error.auth_required else f"The agent reported an error: {error.message}"
    if isinstance(error, AcpConnectionClosed):
        return "The agent process stopped unexpectedly. Send your message again to continue."
    return "The agent turn failed unexpectedly."


@dataclass
class PendingPermission:
    request_id: str
    rpc_id: int | str
    tool_call: dict[str, Any]
    options: list[dict[str, Any]]

    def interrupt_value(self) -> dict[str, Any]:
        locations = self.tool_call.get("locations")
        paths = [item.get("path") for item in locations if isinstance(item, dict)] if isinstance(locations, list) else []
        options = [_public_option(option) for option in self.options]
        return {
            "type": PERMISSION_INTERRUPT_TYPE,
            "request_id": self.request_id,
            "tool_call_id": f"acp-permission-{self.request_id}",
            "title": str(self.tool_call.get("title") or "Tool call"),
            "kind": str(self.tool_call.get("kind") or "other"),
            "paths": [str(path) for path in paths if path],
            "raw_input": _clip_value(mcp_call_arguments(self.tool_call)),
            "options": options,
        }

    def option(self, option_id: str) -> dict[str, Any] | None:
        return next((option for option in self.options if option.get("optionId") == option_id), None)


@dataclass
class PendingHandoff:
    """A K41 tool call waiting while the user controls the browser."""

    request_id: str
    payload: dict[str, Any]
    decision: Future[str]

    def interrupt_value(self) -> dict[str, Any]:
        return self.payload


@dataclass
class PendingToolPermission:
    """A K41 operation waiting for its own permission decision."""

    request_id: str
    payload: dict[str, Any]
    decision: Future[str]

    def interrupt_value(self) -> dict[str, Any]:
        return self.payload


def _public_option(option: dict[str, Any]) -> dict[str, str]:
    public = {
        "id": str(option.get("optionId") or ""),
        "label": str(option.get("name") or option.get("optionId") or ""),
        "kind": str(option.get("kind") or ""),
    }
    meta = option.get("_meta")
    warning = meta.get("agy.security.warning") if isinstance(meta, dict) else None
    if isinstance(warning, dict) and warning.get("message"):
        public["warning"] = str(warning["message"])
    return public


def permission_request_event(value: dict[str, Any], interrupt_id: str = "") -> dict[str, Any]:
    """Project an ACP permission interrupt onto the dashboard choice UI."""
    lines = [value.get("title") or "Tool call"]
    if value.get("paths"):
        lines.append("Paths: " + ", ".join(value["paths"]))
    # Approvals must show the operation in full, so raw input is not abridged here.
    if value.get("raw_input") is not None:
        lines.append("Input: " + json.dumps(value["raw_input"], ensure_ascii=False, default=str))
    options = [{"id": option["id"], "label": option["label"]} for option in value.get("options", [])]
    options.append({"id": STOP_TURN_OPTION_ID, "label": "Stop this turn"})
    lines.extend(f"Warning: {option['warning']}" for option in value.get("options", []) if option.get("warning"))
    return {
        "type": "user_input_request",
        "tool_call_id": value.get("tool_call_id"),
        "interrupt_id": interrupt_id,
        "title": f"Agent tool approval ({value.get('kind') or 'other'})",
        "questions": [{
            "id": PERMISSION_QUESTION_PREFIX + str(value["request_id"]),
            "question": "\n\n".join(str(line) for line in lines),
            "selection_mode": "single",
            "required": True,
            "options": options,
            "free_text": {"enabled": False},
        }],
        "submit_label": "Apply decision",
    }


def _select_automatic_option(options: list[dict[str, Any]], kinds: tuple[str, ...]) -> str | None:
    for kind in kinds:
        for option in options:
            if option.get("kind") == kind and option.get("optionId"):
                return str(option["optionId"])
    return None


class AcpTurn:
    """One ``session/prompt`` call and everything it produced."""

    def __init__(
        self,
        *,
        turn_id: str,
        thread_id: str,
        session_id: str,
        agent_id: str,
        client: AcpClient,
        permission_context=None,
        native_grants: frozenset[str] = frozenset(),
        can_interrupt: bool = False,
        on_done: Callable[[AcpTurn], None] | None = None,
        tools: SessionTools | None = None,
        commit_stopped: Callable[[AcpTurn], Awaitable[None]] | None = None,
    ) -> None:
        self.turn_id = turn_id
        self.thread_id = thread_id
        self.session_id = session_id
        self.agent_id = agent_id
        self.client = client
        self.permission_context = permission_context
        self.native_grants = set(native_grants)
        self.can_interrupt = can_interrupt
        self.on_done = on_done
        self.tools = tools
        # Commits this turn when it was stopped and no graph run can do it.
        self.commit_stopped = commit_stopped
        self.finished_at: float | None = None
        self.stop_reason: str | None = None
        self.error: BaseException | None = None
        self.committed = False
        # True while a graph node is draining this turn, which is what can commit it.
        self.attached = False
        self.transcript = TurnTranscript(turn_id, thread_id=thread_id, provider_label=agent_id)
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._pending: OrderedDict[str, PendingPermission | PendingHandoff | PendingToolPermission] = OrderedDict()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
        self._done = threading.Event()

    # Lifecycle ---------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self.transcript.cancel_requested

    def start(self, blocks: list[dict[str, Any]], *, notices: list[str] | None = None) -> None:
        for notice in notices or ():
            self.add_notice(notice)
        self.client.subscribe(self.session_id, self)
        future: Future[Any] = self.client.prompt(self.session_id, blocks)
        future.add_done_callback(self._on_prompt_done)

    def add_notice(self, text: str) -> None:
        with self._lock:
            self._events.extend(self.transcript.apply({
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text + "\n\n"},
            }))
        self._notify()

    def cancel(self) -> None:
        """Ask the agent to stop and release every pending permission request."""
        with self._lock:
            if self.done or self.transcript.cancel_requested:
                return
            self.transcript.cancel_requested = True
            pending = list(self._pending.values())
            self._pending.clear()
        if self.tools is not None:
            self.tools.cancel_running()
        try:
            self.client.cancel(self.session_id)
            for request in pending:
                # K41 input requests end with their tool call, cancelled above.
                if isinstance(request, PendingPermission):
                    self.client.respond_permission(request.rpc_id, CANCELLED_OUTCOME)
        except Exception:
            logger.debug("ACP cancel could not be delivered.", exc_info=True)
        self._notify()

    def _on_prompt_done(self, future: Future[Any]) -> None:
        try:
            result = future.result()
            self.stop_reason = str((result or {}).get("stopReason") or "end_turn")
        except BaseException as exc:
            self.error = exc
        with self._lock:
            self._pending.clear()
        if self.tools is not None:
            self.tools.cancel_running()
            self.tools.end_turn(self)
        self.finished_at = time.monotonic()
        self.client.unsubscribe(self.session_id, self)
        self._done.set()
        self._notify()
        if self.on_done:
            try:
                self.on_done(self)
            except Exception:
                logger.exception("ACP turn completion handler failed.")

    # Reader-thread callbacks ------------------------------------------------

    def on_update(self, update: dict[str, Any]) -> None:
        with self._lock:
            self._events.extend(self.transcript.apply(update))
        self._notify()

    def _native_policy(self, tool_call):
        from agent.modules.tools import Permissions, evaluate
        from agent.modules.tools import acp_operations
        context = self.permission_context
        if context is None:
            return "deny", "permission_denied: ACP policy context is missing."
        try:
            operations = acp_operations(tool_call, context.workspace)
        except ValueError as exc:
            return "deny", str(exc)
        decisions = [(evaluate(context, action, resource, risk=risk),
                      Permissions.grant_key(context, action, resource, {"arguments": tool_call.get("rawInput")}))
                     for action, resource, risk in operations]
        if any(effect == "deny" for effect, _ in decisions):
            return "deny", "permission_denied: ACP operation is blocked by policy."
        if any(effect == "ask" and key[:32] not in self.native_grants for effect, key in decisions):
            return "ask", ""
        return "allow", ""

    def update_policy(self, context, grants, can_interrupt):
        """Install a graph-thread policy snapshot before processing a resume."""
        with self._lock:
            self.permission_context = context
            self.native_grants = set(grants)
            self.can_interrupt = can_interrupt

    def on_permission_request(self, rpc_id: int | str, params: dict[str, Any]) -> None:
        # Reader-thread path: pure snapshot evaluation, never interrupt or storage I/O.
        options = [item for item in params.get("options") or [] if isinstance(item, dict)]
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        if any("agy.security.warning" in str(option.get("_meta", {})) for option in options):
            meta = tool_call.get("_meta")
            tool_call = {**tool_call, "_meta": {**(meta if isinstance(meta, dict) else {}), "agy.security.warning": True}}
        bridged = bridged_tool_name(tool_call) if self.tools is not None else None
        effect, reason = ("allow", "") if bridged else self._native_policy(tool_call)
        once = _select_automatic_option(options, _ALLOW_KINDS)
        if once is None:
            effect, reason = "deny", "permission_denied: ACP provider offers no allow_once option."
        if effect == "ask" and not self.can_interrupt:
            effect, reason = "deny", "approval_unavailable: This surface cannot deliver ACP tool approvals."
        with self._lock:
            tool_id = str(tool_call.get("toolCallId") or "")
            if tool_id:
                self.transcript.merge_tool(self.transcript.tool_item(tool_id),
                                           {key: value for key, value in tool_call.items() if key != "status"})
            cancelled = self.transcript.cancel_requested
            if not cancelled and effect == "ask":
                identifier = hashlib.sha256(f"{self.turn_id}\0{rpc_id}".encode()).hexdigest()[:32]
                # Conversation grants are kept by K41; the provider only hears allow_once.
                public_options = [item for item in options if item.get("kind") != "allow_always"]
                warning = next((item["_meta"] for item in options if isinstance(item.get("_meta"), dict)
                                and item["_meta"].get("agy.security.warning")), {})
                public_options.append({"optionId": "__allow_thread__", "name": GRANT_LABEL, "kind": "allow_always", "_meta": warning})
                self._pending[identifier] = PendingPermission(identifier, rpc_id, tool_call, public_options)
        if cancelled:
            self.client.respond_permission(rpc_id, CANCELLED_OUTCOME)
        elif effect == "ask":
            self._notify()
        elif effect == "allow":
            if bridged:
                self.tools.approve(tool_call, bridged)
            self.client.select_option(rpc_id, once)
        else:
            self.add_notice(reason)
            decision = _select_automatic_option(options, _REJECT_KINDS)
            if decision:
                self.client.select_option(rpc_id, decision)
            else:
                self.client.respond_permission(rpc_id, CANCELLED_OUTCOME)

    # Node-side API ----------------------------------------------------------

    def take_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events, self._events = self._events, []
            return events

    def next_pending(self) -> PendingPermission | PendingHandoff | PendingToolPermission | None:
        with self._lock:
            return next(iter(self._pending.values()), None)

    def has_unresolved_work(self) -> bool:
        with self._lock:
            return not self.done or bool(self._pending)

    def answer(self, request_id: str, option_id: str) -> bool:
        """Answer a pending request. Options that were not offered are refused."""
        with self._lock:
            request = self._pending.get(request_id)
            option = request.option(option_id) if isinstance(request, PendingPermission) else None
            if option is None:
                return False
            del self._pending[request_id]
        if option.get("kind") in {"allow_once", "allow_always"}:
            effect, reason = self._native_policy(request.tool_call)
            if effect == "deny":
                self.add_notice(reason)
                self.client.respond_permission(request.rpc_id, CANCELLED_OUTCOME)
                return True
            if option.get("kind") == "allow_always":
                from agent.modules.tools import Permissions
                from agent.modules.tools import acp_operations
                from agent.modules.tools import get_coding_service
                from agent.modules.tools import atomic_json
                context = self.permission_context
                for action, resource, _ in acp_operations(request.tool_call, context.workspace):
                    key = Permissions.grant_key(context, action, resource, {"arguments": request.tool_call.get("rawInput")})
                    self.native_grants.add(key[:32])
                    path = get_coding_service().storage.owner_dir(context) / f"{key[:32]}.grant"
                    atomic_json(path, {"action": action, "resource": resource, "key": key})
            option_id = _select_automatic_option(request.options, _ALLOW_KINDS)
            if option_id is None:
                self.add_notice("permission_denied: ACP provider offers no allow_once option.")
                self.client.respond_permission(request.rpc_id, CANCELLED_OUTCOME)
                return True
        self.client.select_option(request.rpc_id, option_id)
        self._notify()
        return True

    async def wait_for_handoff(self, payload: dict[str, Any]) -> str:
        """Show a browser handoff to the user and return their decision."""
        request = PendingHandoff(str(payload["handoff_id"]), payload, Future())
        return await self._wait_for_input(request, cancelled="cancel")

    async def wait_for_tool_permission(self, payload: dict[str, Any]) -> str:
        """Keep an MCP call open while the dashboard approves its K41 operation."""
        request = PendingToolPermission(str(payload["request_id"]), payload, Future())
        return await self._wait_for_input(request, cancelled="deny")

    async def _wait_for_input(self, request: PendingHandoff | PendingToolPermission, *, cancelled: str) -> str:
        with self._lock:
            if self.done or self.transcript.cancel_requested:
                return cancelled
            self._pending[request.request_id] = request
        self._notify()
        try:
            return await asyncio.wrap_future(request.decision)
        finally:
            with self._lock:
                self._pending.pop(request.request_id, None)
            self._notify()

    def answer_handoff(self, request_id: str, decision: str) -> bool:
        return self._answer_input(request_id, decision, PendingHandoff)

    def answer_tool_permission(self, request_id: str, decision: str) -> bool:
        return self._answer_input(request_id, decision, PendingToolPermission)

    def _answer_input(self, request_id: str, decision: str,
                      request_type: type[PendingHandoff] | type[PendingToolPermission]) -> bool:
        with self._lock:
            request = self._pending.get(request_id)
            if not isinstance(request, request_type):
                return False
            del self._pending[request_id]
        try:
            request.decision.set_result(decision)
        except InvalidStateError:
            # The tool call was cancelled at the same moment, releasing its input request.
            return False
        self._notify()
        return True

    def add_event(self, event: dict[str, Any]) -> None:
        """Queue a stream event for whichever node is draining this turn."""
        with self._lock:
            self._events.append(event)
        self._notify()

    def record_tool_output(self, tool_call_id: str, text: str) -> None:
        """Keep what a K41 tool returned. The agent reports the call as finished after this."""
        with self._lock:
            self.transcript.tool_item(tool_call_id).bridged_output = text

    async def wait_for_progress(self) -> None:
        """Block until new events, a pending request, or the end of the turn."""
        event = asyncio.Event()
        entry = (asyncio.get_running_loop(), event)
        with self._lock:
            if self._events or self._pending or self.done:
                return
            self._waiters.append(entry)
        try:
            await event.wait()
        finally:
            with self._lock:
                if entry in self._waiters:
                    self._waiters.remove(entry)

    def _notify(self) -> None:
        with self._lock:
            waiters, self._waiters = self._waiters, []
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass

    # Results -----------------------------------------------------------------

    def result_notice(self) -> str:
        if self.error is not None:
            return describe_failure(self.error)
        if self.stop_reason in _STOP_NOTICES:
            return _STOP_NOTICES[self.stop_reason]
        if self.stop_reason not in _NORMAL_STOPS and not self.transcript.ends_with_text:
            return f"The agent stopped with status '{self.stop_reason}'."
        if self.stop_reason == "end_turn" and not self.transcript.ends_with_text:
            return NO_FINAL_TEXT_NOTICE
        return ""

    def messages(self, *, notice: str = "") -> list[BaseMessage]:
        with self._lock:
            return self.transcript.messages(notice=notice)

    def final_text(self, notice: str = "") -> str:
        with self._lock:
            text = self.transcript.final_text if self.transcript.ends_with_text else ""
        return "\n\n".join(part for part in (text, notice) if part)
