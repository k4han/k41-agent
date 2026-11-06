"""Container-scoped owner of ACP processes, sessions and turns."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.modules.acp.agents import get_agent_definition
from agent.modules.acp.client import AcpClient
from agent.modules.acp.errors import AcpError, AcpNotInstalled, AcpRpcError
from agent.modules.acp.process import AgentCommand
from agent.modules.acp.tool_bridge import SessionTools, ToolBridge, ToolProvider
from agent.modules.acp.turns import AcpTurn, PendingHandoff

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 600.0
FINISHED_TURN_TTL_SECONDS = 3600.0
STOP_COMMIT_TIMEOUT_SECONDS = 10.0
SUPERSEDE_TIMEOUT_SECONDS = 10.0

CommandResolver = Callable[[str], AgentCommand]


def acp_data_root() -> Path:
    home = os.environ.get("K41_AGENT_HOME") or os.environ.get("AGENT_HOME") or str(Path.home() / ".k41-agent")
    return Path(home) / "acp"


@dataclass
class TurnRequest:
    """Everything a new turn needs from the graph."""

    turn_id: str
    thread_id: str
    agent_id: str
    cwd: str
    blocks: list[dict[str, Any]]
    session_id: str = ""
    session_cwd: str = ""
    auto_allow: bool = False
    can_interrupt: bool = False
    config_options: dict[str, str] | None = None
    system_prompt: Callable[[], Awaitable[str]] | None = None
    tools: ToolProvider | None = None
    commit_stopped: Callable[[AcpTurn], Awaitable[None]] | None = None


class AcpService:
    def __init__(
        self,
        *,
        command_resolver: CommandResolver | None = None,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
    ) -> None:
        self._resolve_command = command_resolver or self._installed_command
        self.idle_seconds = idle_seconds
        # A checkpoint from another boot proves its turn ran in a process that
        # is gone, so its prompt must never be sent again.
        self.boot_id = uuid.uuid4().hex
        self._clients: dict[str, AcpClient] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}
        self._live_sessions: dict[int, set[str]] = {}
        self._turns: dict[str, AcpTurn] = {}
        self._started_turn_ids: set[str] = set()
        self._active_by_thread: dict[str, str] = {}
        self._thread_sessions: dict[str, str] = {}
        self._stranded_commits: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._idle_handles: dict[str, asyncio.TimerHandle] = {}
        self._holds: dict[str, int] = {}
        self._applied_options: dict[str, dict[str, str]] = {}
        self._option_categories: dict[str, dict[str, str]] = {}
        self._tool_bridge = ToolBridge()
        self._session_tools: dict[str, SessionTools] = {}
        self._closed = False

    @staticmethod
    def _installed_command(agent_id: str) -> AgentCommand:
        from agent.modules.acp.installer import resolve_installed_command

        return resolve_installed_command(agent_id)

    # Client lifetime --------------------------------------------------------

    async def client_for(self, agent_id: str, *, on_stderr_line: Callable[[str], None] | None = None) -> AcpClient:
        self._loop = asyncio.get_running_loop()
        lock = self._client_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            client = self._clients.get(agent_id)
            if client is not None and not client.closed:
                self._cancel_idle(agent_id)
                return client
            if client is not None:
                # It died by itself. Stopping it still releases its job and pipes.
                self._live_sessions.pop(id(client), None)
                await asyncio.to_thread(client.stop)
            command = self._resolve_command(agent_id)
            client = await asyncio.to_thread(AcpClient, command, on_stderr_line=on_stderr_line)
            try:
                await client.initialize()
            except BaseException:
                await asyncio.to_thread(client.stop)
                raise
            self._clients[agent_id] = client
            self._live_sessions[id(client)] = set()
            return client

    async def restart_client(self, agent_id: str) -> None:
        """Stop an agent so its next use picks up new credentials or a new version."""
        lock = self._client_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            client = self._clients.pop(agent_id, None)
            if client is not None:
                self._live_sessions.pop(id(client), None)
                await asyncio.to_thread(client.stop)

    def agent_is_busy(self, agent_id: str) -> bool:
        if self._holds.get(agent_id):
            return True
        return any(turn.agent_id == agent_id and turn.has_unresolved_work() for turn in self._turns.values())

    def hold(self, agent_id: str) -> None:
        """Keep an agent process alive while something outside a turn uses it."""
        self._holds[agent_id] = self._holds.get(agent_id, 0) + 1
        self._cancel_idle(agent_id)

    def release(self, agent_id: str) -> None:
        self._holds[agent_id] = max(0, self._holds.get(agent_id, 0) - 1)
        self._schedule_idle(agent_id)

    def _cancel_idle(self, agent_id: str) -> None:
        handle = self._idle_handles.pop(agent_id, None)
        if handle is not None:
            handle.cancel()

    def _schedule_idle(self, agent_id: str) -> None:
        if self._closed or self._loop is None or self._loop.is_closed():
            return
        self._cancel_idle(agent_id)
        self._idle_handles[agent_id] = self._loop.call_later(
            self.idle_seconds, lambda: asyncio.ensure_future(self._reap(agent_id)),
        )

    async def _reap(self, agent_id: str) -> None:
        self._idle_handles.pop(agent_id, None)
        if not self.agent_is_busy(agent_id):
            await self.restart_client(agent_id)

    async def close(self) -> None:
        self._closed = True
        for agent_id in list(self._idle_handles):
            self._cancel_idle(agent_id)
        for turn in list(self._turns.values()):
            turn.cancel()
        commits = list(self._stranded_commits)
        for task in commits:
            task.cancel()
        await asyncio.gather(*commits, return_exceptions=True)
        clients = list(self._clients.values())
        self._clients.clear()
        self._live_sessions.clear()
        await asyncio.gather(*(asyncio.to_thread(client.stop) for client in clients), return_exceptions=True)
        await self._tool_bridge.close()

    # Turns ------------------------------------------------------------------

    def turn(self, turn_id: str) -> AcpTurn | None:
        return self._turns.get(turn_id)

    def was_started(self, turn_id: str) -> bool:
        return turn_id in self._started_turn_ids

    def active_turn(self, thread_id: str) -> AcpTurn | None:
        turn_id = self._active_by_thread.get(thread_id)
        turn = self._turns.get(turn_id) if turn_id else None
        return turn if turn is not None and turn.has_unresolved_work() else None

    def has_active_turn(self, thread_id: str) -> bool:
        return self.active_turn(thread_id) is not None

    def _prune_finished(self) -> None:
        now = time.monotonic()
        for turn_id, turn in list(self._turns.items()):
            if turn.finished_at is not None and now - turn.finished_at > FINISHED_TURN_TTL_SECONDS:
                del self._turns[turn_id]

    def _on_turn_done(self, turn: AcpTurn) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._schedule_idle, turn.agent_id)
            except RuntimeError:
                pass

    async def begin_turn(self, request: TurnRequest) -> AcpTurn:
        """Prepare a session and send the prompt exactly once."""
        self._prune_finished()
        # Recorded before any I/O: a failure part-way must not allow a re-send.
        self._started_turn_ids.add(request.turn_id)
        client = await self.client_for(request.agent_id)
        try:
            self.hold(request.agent_id)
            session_id, blocks, notices = await self._prepare_session(client, request)
            tools = self._session_tools.get(session_id)
            turn = AcpTurn(
                turn_id=request.turn_id,
                thread_id=request.thread_id,
                session_id=session_id,
                agent_id=request.agent_id,
                client=client,
                auto_allow=request.auto_allow,
                can_interrupt=request.can_interrupt,
                on_done=self._on_turn_done,
                tools=tools,
                commit_stopped=request.commit_stopped,
            )
            if tools is not None:
                tools.begin_turn(turn, request.tools)
            self._turns[request.turn_id] = turn
            self._active_by_thread[request.thread_id] = request.turn_id
            self._thread_sessions[request.thread_id] = session_id
            turn.start(blocks, notices=notices)
            return turn
        finally:
            self.release(request.agent_id)

    async def _prepare_session(self, client: AcpClient, request: TurnRequest) -> tuple[str, list[dict[str, Any]], list[str]]:
        notices: list[str] = []
        tool_names = await self._wanted_tool_names(client, request, notices)
        live = self._live_sessions.setdefault(id(client), set())
        session_id = ""
        result: dict[str, Any] = {}
        if request.session_id and request.session_cwd == request.cwd:
            if request.session_id in live and self._attached_tools_match(request.session_id, tool_names):
                session_id = request.session_id
            else:
                # A live session also takes this path when the card's tools changed or
                # their endpoint moved: session/resume makes the agent connect again.
                session_id, result, notice = await self._reattach(client, request, tool_names)
                if notice:
                    notices.append(notice)
        elif request.session_id:
            notices.append("The workspace changed, so the agent started a new session without earlier context.")
        if request.session_id and session_id != request.session_id:
            # The thread moves to a new session and nothing uses this one again.
            self._forget_session(request.session_id)
        blocks = [block for block in request.blocks if block.get("type") != "image" or client.supports_image()]
        if not session_id:
            prompt_text = await request.system_prompt() if request.system_prompt else ""
            meta = None
            if prompt_text.strip() and client.supports_system_prompt_append():
                meta = {"antigravity": {"systemPrompt": {"append": prompt_text}}}
            elif prompt_text.strip():
                blocks.insert(0, {"type": "text", "text": f"<system_instructions>\n{prompt_text}\n</system_instructions>"})
            tools = self._tool_bridge.open_session(tool_names, request.tools) if tool_names else None
            try:
                result = await client.new_session(request.cwd, meta=meta, mcp_servers=self._mcp_servers(tools))
                session_id = str(result.get("sessionId") or "")
                if not session_id:
                    raise AcpError("The agent did not return a session id.")
            except BaseException:
                if tools is not None:
                    self._tool_bridge.revoke(tools)
                raise
            self._bind_tools(session_id, tools)
        live.add(session_id)
        notices.extend(await self._apply_config_options(client, session_id, result, request.config_options))
        return session_id, blocks, notices

    async def _wanted_tool_names(self, client: AcpClient, request: TurnRequest, notices: list[str]) -> frozenset[str]:
        """Names of the K41 tools this turn should offer. A problem here only costs the tools."""
        if request.tools is None:
            return frozenset()
        if not client.supports_http_mcp():
            notices.append("This agent cannot connect to K41 tools, so it runs with its own tools only.")
            return frozenset()
        try:
            names = frozenset(tool["name"] for tool in await request.tools.list_tools())
            if names:
                await self._tool_bridge.ensure_started()
            return names
        except Exception as exc:
            logger.warning("K41 tools are unavailable for an ACP turn: %s", exc, exc_info=not isinstance(exc, AcpError | OSError | ValueError))
            notices.append(f"K41 tools are unavailable for this message: {exc}")
            return frozenset()

    def _attached_tools_match(self, session_id: str, wanted: frozenset[str]) -> bool:
        tools = self._session_tools.get(session_id)
        if tools is None:
            return not wanted
        return tools.names == wanted and self._tool_bridge.is_reachable(tools)

    def _mcp_servers(self, tools: SessionTools | None) -> list[dict[str, Any]]:
        return [self._tool_bridge.server_entry(tools)] if tools is not None else []

    def _bind_tools(self, session_id: str, tools: SessionTools | None) -> None:
        previous = self._session_tools.pop(session_id, None)
        if previous is not None:
            self._tool_bridge.revoke(previous)
        if tools is not None:
            if previous is not None:
                tools.inherit_grants(previous)
            self._session_tools[session_id] = tools

    def _forget_session(self, session_id: str) -> None:
        self._bind_tools(session_id, None)
        self._applied_options.pop(session_id, None)
        self._option_categories.pop(session_id, None)
        for live in self._live_sessions.values():
            live.discard(session_id)

    def forget_thread(self, thread_id: str) -> None:
        """End what a deleted conversation still holds. The agent's session itself just stays unused."""
        turn = self.active_turn(thread_id)
        if turn is not None:
            turn.cancel()
        self._active_by_thread.pop(thread_id, None)
        session_id = self._thread_sessions.pop(thread_id, None)
        if session_id:
            self._forget_session(session_id)

    async def _reattach(self, client: AcpClient, request: TurnRequest, tool_names: frozenset[str]) -> tuple[str, dict[str, Any], str]:
        lost = "The earlier agent session could not be restored, so this one starts without earlier context."
        if not client.supports_resume() and not client.supports_load():
            return "", {}, lost
        tools = self._tool_bridge.open_session(tool_names, request.tools) if tool_names else None
        try:
            # No listener is subscribed yet, so history replayed by session/load
            # is dropped instead of being shown again.
            if client.supports_resume():
                result = await client.resume_session(request.session_id, request.cwd, mcp_servers=self._mcp_servers(tools))
            else:
                result = await client.load_session(request.session_id, request.cwd, mcp_servers=self._mcp_servers(tools))
        except AcpRpcError as exc:
            if tools is not None:
                self._tool_bridge.revoke(tools)
            if exc.auth_required:
                raise
            logger.info("ACP session could not be restored (code %s).", exc.code)
            return "", {}, lost
        except BaseException:
            if tools is not None:
                self._tool_bridge.revoke(tools)
            raise
        self._bind_tools(request.session_id, tools)
        return request.session_id, result, ""

    async def _apply_config_options(
        self,
        client: AcpClient,
        session_id: str,
        session_result: dict[str, Any],
        wanted: dict[str, str] | None,
    ) -> list[str]:
        self._record_options(session_id, session_result)
        if not wanted:
            return []
        applied = self._applied_options.setdefault(session_id, {})
        categories = self._option_categories.get(session_id, {})
        notices: list[str] = []
        # A model change can reset what depends on it, so the model is set first.
        for config_id, value in sorted(wanted.items(), key=lambda item: categories.get(item[0]) != "model"):
            if categories.get(config_id) == "mode":
                # Tool approvals belong to the card's permission_mode.
                notices.append(f"The agent kept its current '{config_id}' setting because K41 controls tool approvals.")
                continue
            if applied.get(config_id) == value:
                continue
            try:
                self._record_options(session_id, await client.set_config_option(session_id, config_id, value))
                applied[config_id] = value
            except AcpRpcError as exc:
                if exc.auth_required:
                    raise
                notices.append(f"The agent kept its current '{config_id}' setting because '{value}' is not available.")
        return notices

    def _record_options(self, session_id: str, result: dict[str, Any]) -> None:
        """Remember what the agent reported; a live session reports nothing until it changes."""
        for option in result.get("configOptions") or []:
            if isinstance(option, dict):
                config_id = str(option.get("id"))
                self._applied_options.setdefault(session_id, {})[config_id] = option.get("currentValue")
                self._option_categories.setdefault(session_id, {})[config_id] = str(option.get("category") or "")

    async def stop_thread(self, thread_id: str, *, timeout: float = STOP_COMMIT_TIMEOUT_SECONDS) -> bool:
        """Cancel a thread's turn and wait for its node to commit the partial output.

        Returns True when the run finished by itself, so the caller has no task
        to cancel. Cancelling that task could lose the commit in flight.
        """
        turn = self.active_turn(thread_id)
        if turn is None:
            return False
        stranded = self._stop_turn(turn)
        if not turn.attached and not stranded:
            # Paused on a permission card: no run is active, so there is no task to
            # cancel. Answering the card later commits the partial output.
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if turn.done and turn.committed:
                return True
            await asyncio.sleep(0.05)
        return stranded

    def cancel_thread_now(self, thread_id: str) -> bool:
        """Signal cancellation without waiting. Usable from synchronous code."""
        turn = self.active_turn(thread_id)
        if turn is None:
            return False
        self._stop_turn(turn)
        return True

    def _stop_turn(self, turn: AcpTurn) -> bool:
        """Cancel a turn and return whether it gets committed without a graph run.

        A permission card outlives the cancel, and answering it resumes the graph.
        A handoff ends with its tool call, which leaves nothing to resume a graph
        paused on it, so its output would stay uncommitted and the graph paused.
        """
        stranded = (
            not turn.attached
            and turn.commit_stopped is not None
            and self._loop is not None
            and isinstance(turn.next_pending(), PendingHandoff)
        )
        turn.cancel()
        if stranded:
            # The caller's context holds the container that the commit needs.
            self._loop.call_soon_threadsafe(self._start_stranded_commit, turn, context=contextvars.copy_context())
        return stranded

    def _start_stranded_commit(self, turn: AcpTurn) -> None:
        task = asyncio.ensure_future(self._commit_stranded(turn))
        self._stranded_commits.add(task)
        task.add_done_callback(self._stranded_commits.discard)

    async def _commit_stranded(self, turn: AcpTurn) -> None:
        await self._end_cancelled_turn(turn)
        try:
            await turn.commit_stopped(turn)
        except Exception:
            logger.exception("The output of a stopped ACP turn could not be committed.")

    async def supersede(self, thread_id: str) -> None:
        """End a thread's previous turn before a new prompt starts."""
        turn = self.active_turn(thread_id)
        if turn is None:
            return
        turn.cancel()
        await self._end_cancelled_turn(turn)

    async def _end_cancelled_turn(self, turn: AcpTurn) -> None:
        deadline = time.monotonic() + SUPERSEDE_TIMEOUT_SECONDS
        while not turn.done and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if not turn.done:
            # The agent ignored the cancel. A new prompt in the same session would
            # interleave with the old one, so the process has to go.
            logger.warning("ACP agent ignored a cancel; restarting it.")
            await self.restart_client(turn.agent_id)


def active_turn_threads(service: AcpService | None, thread_ids: Iterable[str]) -> set[str]:
    return {thread_id for thread_id in thread_ids if service is not None and service.has_active_turn(thread_id)}


def get_acp_service(container: Any | None = None) -> AcpService:
    """Return the container-scoped service, creating it on first use."""
    if container is None:
        from agent.bootstrap.container import require_active_container

        container = require_active_container()
    with container._lazy_lock:
        if container._acp_service is None:
            container._acp_service = AcpService()
        return container._acp_service


def peek_acp_service() -> AcpService | None:
    """Return the service only if it already exists, for cleanup-style hooks."""
    from agent.bootstrap.container import get_active_container

    container = get_active_container()
    return getattr(container, "_acp_service", None) if container is not None else None


def not_installed_notice(agent_id: str) -> str:
    definition = get_agent_definition(agent_id)
    name = definition.display_name if definition else agent_id
    return f"{name} is not installed. Install it under Settings > ACP agents, then send your message again."


__all__ = [
    "AcpNotInstalled",
    "AcpService",
    "TurnRequest",
    "acp_data_root",
    "active_turn_threads",
    "get_acp_service",
    "not_installed_notice",
    "peek_acp_service",
]
