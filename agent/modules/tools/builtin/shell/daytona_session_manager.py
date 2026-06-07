"""Daytona-backed persistent terminal sessions."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Dict, List

from agent.modules.tools.builtin.shell.session_manager import (
    MAX_HISTORY_LINES,
    MAX_OUTPUT_CHARS,
    _strip_ansi,
)
from agent.modules.workspaces import (
    DAYTONA_BACKEND,
    WorkspaceRef,
    get_workspace_command_executor,
    get_workspace_backend_registry,
)

logger = logging.getLogger(__name__)


def _get_daytona_backend_type() -> Any:
    return get_workspace_backend_registry().load_backend_type(DAYTONA_BACKEND)


@dataclass
class DaytonaTerminalSession:
    session_id: str
    pty_id: str
    scope_id: str | None
    workspace: WorkspaceRef
    working_dir: str
    backend: Any
    pty_handle: Any
    output_queue: Queue[str]
    error_queue: Queue[str]
    is_running: bool = True
    has_background_process: bool = False
    output_history: deque[str] = field(
        default_factory=lambda: deque(maxlen=MAX_HISTORY_LINES)
    )


class DaytonaTerminalSessionManager:
    """Manages Daytona PTY sessions scoped by thread and session ID."""

    def __init__(self) -> None:
        self.sessions: Dict[str, DaytonaTerminalSession] = {}

    @staticmethod
    def _normalize_scope_id(scope_id: str | None) -> str | None:
        normalized = str(scope_id or "").strip()
        return normalized or None

    @classmethod
    def _session_key(cls, session_id: str, scope_id: str | None = None) -> str:
        normalized_scope_id = cls._normalize_scope_id(scope_id)
        if normalized_scope_id is None:
            return session_id
        return f"{normalized_scope_id}\x1f{session_id}"

    @staticmethod
    def _is_thread_tree_scope(scope_id: str | None, thread_id: str) -> bool:
        if not scope_id:
            return False
        return scope_id == thread_id or scope_id.startswith(f"{thread_id}:sub:")

    @staticmethod
    def _thread_id_from_scope(scope_id: str | None) -> str | None:
        normalized = str(scope_id or "").strip()
        if not normalized:
            return None
        return normalized.split(":sub:", 1)[0]

    @staticmethod
    def _pty_id(session_key: str) -> str:
        digest = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:16]
        return f"kaka-{digest}"

    def has_session(self, session_id: str, scope_id: str | None = None) -> bool:
        return self._session_key(session_id, scope_id) in self.sessions

    def create_session(
        self,
        *,
        workspace: WorkspaceRef,
        session_name: str | None = None,
        scope_id: str | None = None,
    ) -> str:
        session_id = session_name or f"term_{uuid.uuid4().hex[:8]}"
        normalized_scope_id = self._normalize_scope_id(scope_id)
        session_key = self._session_key(session_id, normalized_scope_id)
        backend_type = _get_daytona_backend_type()
        backend = backend_type(
            workspace,
            thread_id=self._thread_id_from_scope(normalized_scope_id),
        )
        pty_id = self._pty_id(session_key)
        pty_handle = self._create_or_connect_pty(backend, pty_id)

        output_queue: Queue[str] = Queue()
        error_queue: Queue[str] = Queue()
        session = DaytonaTerminalSession(
            session_id=session_id,
            pty_id=pty_id,
            scope_id=normalized_scope_id,
            workspace=workspace,
            working_dir=backend.root,
            backend=backend,
            pty_handle=pty_handle,
            output_queue=output_queue,
            error_queue=error_queue,
        )
        self.sessions[session_key] = session
        self._start_reader(session)
        time.sleep(0.2)
        self._drain_queues_to_history(session)
        return session_id

    def _create_or_connect_pty(
        self, backend: Any, pty_id: str
    ) -> Any:
        try:
            return backend.process.create_pty_session(
                id=pty_id,
                cwd=backend.root,
                envs={"TERM": "xterm-256color"},
            )
        except TypeError:
            return backend.process.create_pty_session(pty_id, cwd=backend.root)
        except Exception:
            connect = getattr(backend.process, "connect_pty_session", None)
            if callable(connect):
                return connect(pty_id)
            raise

    def _start_reader(self, session: DaytonaTerminalSession) -> None:
        import threading

        def read_output() -> None:
            try:
                for chunk in session.pty_handle:
                    if chunk is None:
                        continue
                    text = self._decode_chunk(chunk)
                    for line in text.splitlines():
                        if line:
                            session.output_queue.put(_strip_ansi(line.rstrip()))
            except Exception as exc:
                if session.is_running:
                    session.error_queue.put(f"Error reading PTY output: {exc}")

        threading.Thread(target=read_output, daemon=True).start()

    @staticmethod
    def _decode_chunk(chunk: Any) -> str:
        if isinstance(chunk, bytes):
            return chunk.decode("utf-8", errors="replace")
        return str(chunk)

    def _drain_queues_to_history(self, session: DaytonaTerminalSession) -> None:
        try:
            while not session.output_queue.empty():
                line = session.output_queue.get_nowait()
                session.output_history.append(line)
            while not session.error_queue.empty():
                line = session.error_queue.get_nowait()
                session.output_history.append(f"[stderr] {line}")
        except Empty:
            pass

    @staticmethod
    def _truncate_output(text: str) -> str:
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        truncated_chars = len(text) - MAX_OUTPUT_CHARS
        return f"[...truncated {truncated_chars} characters...]\n{text[-MAX_OUTPUT_CHARS:]}"

    def execute_command(
        self,
        *,
        session_id: str,
        command: str,
        workspace: WorkspaceRef,
        timeout: float = 30.0,
        run_in_background: bool = False,
        force: bool = False,
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        session_key = self._session_key(session_id, scope_id)
        if session_key not in self.sessions:
            try:
                self.create_session(
                    workspace=workspace,
                    session_name=session_id,
                    scope_id=scope_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to create Daytona PTY session %s; falling back to non-PTY "
                    "execution. State (cwd, env) from prior sessions will NOT be preserved: %s",
                    session_id,
                    exc,
                )
                if run_in_background:
                    return {
                        "error": f"Failed to create Daytona PTY session: {exc}",
                        "fallback": "non_pty",
                    }
                fallback = self._execute_without_pty(
                    workspace,
                    command,
                    timeout=timeout,
                    scope_id=scope_id,
                )
                fallback["warning"] = (
                    "Daytona PTY session could not be created; ran command in a fresh "
                    "non-PTY shell. Working directory, environment, and any prior session "
                    "state are not preserved."
                )
                fallback["fallback"] = "non_pty"
                return fallback

        session = self.sessions[session_key]
        if not session.is_running:
            return {"error": f"Session {session_id} is no longer active"}

        session.backend.ensure_active()
        self._drain_queues_to_history(session)
        if session.has_background_process and not force:
            return {
                "warning": (
                    f"Session '{session_id}' has a background process running. "
                    "Use force=True to execute anyway, or use bash_interrupt to stop it, "
                    "or create a new session."
                ),
                "session_id": session_id,
            }

        try:
            if run_in_background:
                self._send_input(session.pty_handle, command + "\n")
                session.has_background_process = True
                return {
                    "session_id": session_id,
                    "command": command,
                    "status": "running_background",
                }

            sentinel_id = uuid.uuid4().hex[:8]
            sentinel_token = f"____CMD_DONE_{sentinel_id}____"
            self._send_input(session.pty_handle, f"{command}\necho {sentinel_token}\n")

            output_lines: list[str] = []
            error_lines: list[str] = []
            total_output_chars = 0
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    line = session.output_queue.get(timeout=0.05)
                    if sentinel_token in line:
                        break
                    total_output_chars += len(line)
                    if total_output_chars <= MAX_OUTPUT_CHARS:
                        output_lines.append(line)
                    elif (
                        not output_lines
                        or output_lines[-1] != "[...output truncated...]"
                    ):
                        output_lines.append("[...output truncated...]")
                except Empty:
                    pass

                try:
                    error_lines.append(session.error_queue.get_nowait())
                except Empty:
                    pass

            output_text = self._truncate_output("\n".join(output_lines))
            return {
                "session_id": session_id,
                "command": command,
                "output": output_text,
                "stderr": "\n".join(error_lines),
                "status": "completed",
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _execute_without_pty(
        self,
        workspace: WorkspaceRef,
        command: str,
        *,
        timeout: float,
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        async def execute() -> Any:
            executor = await get_workspace_command_executor(
                workspace,
                thread_id=self._thread_id_from_scope(scope_id),
            )
            return await executor.execute(
                command,
                timeout=max(1, int(timeout)),
                max_output_chars=MAX_OUTPUT_CHARS,
            )

        try:
            result = self._run_coro_blocking(execute())
        except Exception as exc:
            return {"error": str(exc)}
        return {
            "command": command,
            "output": result.output,
            "stderr": "",
            "status": "completed",
        }

    @staticmethod
    def _run_coro_blocking(coro: Any) -> Any:
        result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

        def runner() -> None:
            try:
                result_queue.put((True, asyncio.run(coro)))
            except Exception as exc:
                result_queue.put((False, exc))

        import threading

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        ok, value = result_queue.get()
        if ok:
            return value
        raise value

    def get_session_output(
        self,
        session_id: str,
        timeout: float = 1.0,
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        session_key = self._session_key(session_id, scope_id)
        if session_key not in self.sessions:
            return {"error": f"Session {session_id} does not exist"}

        session = self.sessions[session_key]
        session.backend.touch()
        output_lines: list[str] = []
        error_lines: list[str] = []
        total_chars = 0
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                line = session.output_queue.get(timeout=0.1)
                total_chars += len(line)
                if total_chars <= MAX_OUTPUT_CHARS:
                    output_lines.append(line)
                elif not output_lines or output_lines[-1] != "[...output truncated...]":
                    output_lines.append("[...output truncated...]")
            except Empty:
                pass

            try:
                error_lines.append(session.error_queue.get_nowait())
            except Empty:
                pass

        return {
            "session_id": session_id,
            "output": self._truncate_output("\n".join(output_lines)),
            "stderr": "\n".join(error_lines),
            "is_running": self._session_is_running(session),
        }

    def send_input(
        self,
        session_id: str,
        text: str,
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        session_key = self._session_key(session_id, scope_id)
        if session_key not in self.sessions:
            return {"error": f"Session '{session_id}' does not exist"}
        session = self.sessions[session_key]
        try:
            session.backend.ensure_active()
            self._send_input(session.pty_handle, text + "\n")
            return {"status": "sent", "session_id": session_id, "text": text}
        except Exception as exc:
            return {"error": f"Failed to send input: {exc}"}

    def send_signal(
        self,
        session_id: str,
        signal_type: str = "interrupt",
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        session_key = self._session_key(session_id, scope_id)
        if session_key not in self.sessions:
            return {"error": f"Session '{session_id}' does not exist"}
        session = self.sessions[session_key]
        try:
            session.backend.ensure_active()
            if signal_type == "interrupt":
                self._send_input(session.pty_handle, "\x03")
            elif signal_type == "terminate":
                self._kill_session(session)
                self.sessions.pop(session_key, None)
            else:
                return {
                    "error": (
                        f"Unknown signal_type: '{signal_type}'. "
                        "Use 'interrupt' or 'terminate'."
                    ),
                }
            session.has_background_process = False
            return {
                "status": "signal_sent",
                "session_id": session_id,
                "signal_type": signal_type,
                "target_pid": "daytona-pty",
            }
        except Exception as exc:
            return {"error": f"Failed to send signal: {exc}"}

    def list_sessions(self, scope_id: str | None = None) -> List[Dict[str, Any]]:
        normalized_scope_id = self._normalize_scope_id(scope_id)
        return [
            {
                "session_id": session.session_id,
                "scope_id": session.scope_id,
                "working_dir": session.working_dir,
                "is_running": self._session_is_running(session),
                "backend": "daytona",
            }
            for session in self.sessions.values()
            if normalized_scope_id is None or session.scope_id == normalized_scope_id
        ]

    def close_session(self, session_id: str, scope_id: str | None = None) -> bool:
        session_key = self._session_key(session_id, scope_id)
        return self._close_session_by_key(session_key)

    def _close_session_by_key(self, session_key: str) -> bool:
        if session_key not in self.sessions:
            return False
        session = self.sessions[session_key]
        session.is_running = False
        try:
            self._send_input(session.pty_handle, "exit\n")
        except Exception:
            pass
        try:
            self._kill_session(session)
        except Exception as exc:
            logger.debug(
                "Failed to kill Daytona PTY session %s: %s", session.pty_id, exc
            )
        self.sessions.pop(session_key, None)
        return True

    def close_all_sessions(self, scope_id: str | None = None) -> int:
        normalized_scope_id = self._normalize_scope_id(scope_id)
        session_keys = [
            key
            for key, session in self.sessions.items()
            if normalized_scope_id is None or session.scope_id == normalized_scope_id
        ]
        for session_key in session_keys:
            self._close_session_by_key(session_key)
        return len(session_keys)

    def close_thread_sessions(self, thread_id: str) -> int:
        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id:
            return 0
        session_keys = [
            key
            for key, session in self.sessions.items()
            if self._is_thread_tree_scope(session.scope_id, normalized_thread_id)
        ]
        for session_key in session_keys:
            self._close_session_by_key(session_key)
        return len(session_keys)

    def _session_is_running(self, session: DaytonaTerminalSession) -> bool:
        is_connected = getattr(session.pty_handle, "is_connected", None)
        if callable(is_connected):
            try:
                return bool(is_connected())
            except Exception:
                pass
        info_getter = getattr(session.backend.process, "get_pty_session_info", None)
        if callable(info_getter):
            try:
                info = info_getter(session.pty_id)
                active = getattr(info, "active", None)
                if active is not None:
                    return bool(active)
            except Exception:
                return session.is_running
        return session.is_running

    @staticmethod
    def _send_input(pty_handle: Any, text: str) -> None:
        sender = getattr(pty_handle, "send_input", None)
        if callable(sender):
            sender(text)
            return
        writer = getattr(pty_handle, "write", None)
        if callable(writer):
            writer(text)
            return
        sender = getattr(pty_handle, "send", None)
        if callable(sender):
            sender(text)
            return
        raise RuntimeError("Daytona PTY handle does not support input.")

    def _kill_session(self, session: DaytonaTerminalSession) -> None:
        killer = getattr(session.backend.process, "kill_pty_session", None)
        if callable(killer):
            killer(session.pty_id)
            return
        killer = getattr(session.pty_handle, "kill", None)
        if callable(killer):
            killer()


daytona_session_manager = DaytonaTerminalSessionManager()


__all__ = [
    "DaytonaTerminalSession",
    "DaytonaTerminalSessionManager",
    "daytona_session_manager",
]
