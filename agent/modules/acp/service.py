"""Container-scoped owner of ACP processes, sessions and turns."""

from __future__ import annotations

import asyncio
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
from agent.modules.acp.turns import AcpTurn

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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._idle_handles: dict[str, asyncio.TimerHandle] = {}
        self._holds: dict[str, int] = {}
        self._applied_options: dict[str, dict[str, str]] = {}
        self._option_categories: dict[str, dict[str, str]] = {}
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
        clients = list(self._clients.values())
        self._clients.clear()
        self._live_sessions.clear()
        await asyncio.gather(*(asyncio.to_thread(client.stop) for client in clients), return_exceptions=True)

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
            turn = AcpTurn(
                turn_id=request.turn_id,
                thread_id=request.thread_id,
                session_id=session_id,
                agent_id=request.agent_id,
                client=client,
                auto_allow=request.auto_allow,
                can_interrupt=request.can_interrupt,
                on_done=self._on_turn_done,
            )
            self._turns[request.turn_id] = turn
            self._active_by_thread[request.thread_id] = request.turn_id
            turn.start(blocks, notices=notices)
            return turn
        finally:
            self.release(request.agent_id)

    async def _prepare_session(self, client: AcpClient, request: TurnRequest) -> tuple[str, list[dict[str, Any]], list[str]]:
        notices: list[str] = []
        live = self._live_sessions.setdefault(id(client), set())
        session_id = ""
        result: dict[str, Any] = {}
        if request.session_id and request.session_cwd == request.cwd:
            if request.session_id in live:
                session_id = request.session_id
            else:
                session_id, result, notice = await self._reattach(client, request)
                if notice:
                    notices.append(notice)
        elif request.session_id:
            notices.append("The workspace changed, so the agent started a new session without earlier context.")
        blocks = [block for block in request.blocks if block.get("type") != "image" or client.supports_image()]
        if not session_id:
            prompt_text = await request.system_prompt() if request.system_prompt else ""
            meta = None
            if prompt_text.strip() and client.supports_system_prompt_append():
                meta = {"antigravity": {"systemPrompt": {"append": prompt_text}}}
            elif prompt_text.strip():
                blocks.insert(0, {"type": "text", "text": f"<system_instructions>\n{prompt_text}\n</system_instructions>"})
            result = await client.new_session(request.cwd, meta=meta)
            session_id = str(result.get("sessionId") or "")
            if not session_id:
                raise AcpError("The agent did not return a session id.")
        live.add(session_id)
        notices.extend(await self._apply_config_options(client, session_id, result, request.config_options))
        return session_id, blocks, notices

    @staticmethod
    async def _reattach(client: AcpClient, request: TurnRequest) -> tuple[str, dict[str, Any], str]:
        lost = "The earlier agent session could not be restored, so this one starts without earlier context."
        if not client.supports_resume() and not client.supports_load():
            return "", {}, lost
        try:
            # No listener is subscribed yet, so history replayed by session/load
            # is dropped instead of being shown again.
            if client.supports_resume():
                result = await client.resume_session(request.session_id, request.cwd)
            else:
                result = await client.load_session(request.session_id, request.cwd)
        except AcpRpcError as exc:
            if exc.auth_required:
                raise
            logger.info("ACP session could not be restored (code %s).", exc.code)
            return "", {}, lost
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
        turn.cancel()
        if not turn.attached:
            # Paused on a permission card: no run is active, so there is no task to
            # cancel. Answering the card later commits the partial output.
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if turn.done and turn.committed:
                return True
            await asyncio.sleep(0.05)
        return False

    def cancel_thread_now(self, thread_id: str) -> bool:
        """Signal cancellation without waiting. Usable from synchronous code."""
        turn = self.active_turn(thread_id)
        if turn is None:
            return False
        turn.cancel()
        return True

    async def supersede(self, thread_id: str) -> None:
        """End a thread's previous turn before a new prompt starts."""
        turn = self.active_turn(thread_id)
        if turn is None:
            return
        turn.cancel()
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
