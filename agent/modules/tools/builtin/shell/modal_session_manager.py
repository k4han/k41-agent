"""Modal-backed shell command execution."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from agent.modules.tools.builtin.shell.session_manager import MAX_OUTPUT_CHARS
from agent.modules.workspaces import WorkspaceRef, get_workspace_command_executor


class ModalCommandSessionManager:
    """Executes shell commands in Modal sandboxes without persistent PTY state."""

    def __init__(self) -> None:
        self.sessions: Dict[str, Any] = {}

    @staticmethod
    def _normalize_scope_id(scope_id: str | None) -> str | None:
        normalized = str(scope_id or "").strip()
        return normalized or None

    @staticmethod
    def _thread_id_from_scope(scope_id: str | None) -> str | None:
        normalized = str(scope_id or "").strip()
        if not normalized:
            return None
        return normalized.split(":sub:", 1)[0]

    @staticmethod
    def _session_key(session_id: str, scope_id: str | None = None) -> str:
        normalized_scope_id = ModalCommandSessionManager._normalize_scope_id(scope_id)
        if normalized_scope_id is None:
            return session_id
        digest = hashlib.sha1(normalized_scope_id.encode("utf-8")).hexdigest()[:12]
        return f"{digest}\x1f{session_id}"

    def has_session(self, session_id: str, scope_id: str | None = None) -> bool:
        return self._session_key(session_id, scope_id) in self.sessions

    async def execute_command(
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
        del session_id, force
        if run_in_background:
            return {
                "error": (
                    "Modal shell execution does not support background sessions yet."
                ),
            }
        try:
            executor = await get_workspace_command_executor(
                workspace,
                thread_id=self._thread_id_from_scope(scope_id),
            )
            result = await executor.execute(
                command,
                timeout=max(1, int(timeout)),
                max_output_chars=MAX_OUTPUT_CHARS,
            )
        except Exception as exc:
            return {"error": str(exc)}
        return {
            "command": command,
            "output": result.output,
            "stderr": "",
            "status": "completed",
        }

    def get_session_output(
        self,
        session_id: str,
        timeout: float = 1.0,
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        del timeout, scope_id
        return {"error": f"Session {session_id} does not exist"}

    def send_input(
        self,
        session_id: str,
        text: str,
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        del text, scope_id
        return {"error": f"Session '{session_id}' does not exist"}

    def send_signal(
        self,
        session_id: str,
        signal_type: str = "interrupt",
        scope_id: str | None = None,
    ) -> Dict[str, Any]:
        del signal_type, scope_id
        return {"error": f"Session '{session_id}' does not exist"}

    def list_sessions(self, scope_id: str | None = None) -> List[Dict[str, Any]]:
        del scope_id
        return []

    def close_session(self, session_id: str, scope_id: str | None = None) -> bool:
        del session_id, scope_id
        return False

    def close_all_sessions(self, scope_id: str | None = None) -> int:
        del scope_id
        return 0

    def close_thread_sessions(self, thread_id: str) -> int:
        del thread_id
        return 0


modal_session_manager = ModalCommandSessionManager()


__all__ = ["ModalCommandSessionManager", "modal_session_manager"]
