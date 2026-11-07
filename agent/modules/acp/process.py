"""Agent process lifetime: spawn, contain, drain stderr, and kill the whole tree."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent.modules.tools import build_safe_env
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)

# Network and locale settings an agent needs, on top of the safe base environment.
_EXTRA_ALLOWED_ENV = {
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "USERNAME", "USERDOMAIN", "HOMEDRIVE", "HOMEPATH",
}
_STOP_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class AgentCommand:
    """How to launch one agent."""

    argv: Sequence[str]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)


def build_agent_env(extra: Mapping[str, str]) -> dict[str, str]:
    """Return a filtered environment plus ``extra``.

    ``build_safe_env`` also filters its ``extra_vars`` and would drop agent
    settings such as ``GEMINI_HOME``, so those are merged afterwards on purpose.
    """
    return {**build_safe_env(extra_allowed=_EXTRA_ALLOWED_ENV), **extra}


def _spawn(command: AgentCommand) -> subprocess.Popen[bytes]:
    options = {
        "cwd": str(command.cwd),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": build_agent_env(command.env),
    }
    if os.name == "nt":
        from agent.modules.tools import spawn_contained_process

        return spawn_contained_process(list(command.argv), track_descendants=False, **options, **hidden_subprocess_kwargs())
    return subprocess.Popen(list(command.argv), start_new_session=True, **options)


class AgentProcess:
    """A running agent. Stderr can hold OAuth values, so it is never logged or kept on disk."""

    def __init__(self, command: AgentCommand) -> None:
        command.cwd.mkdir(parents=True, exist_ok=True)
        self.popen = _spawn(command)
        self.on_stderr_line: Callable[[str], None] | None = None
        self._stderr_thread = threading.Thread(target=self._drain_stderr, name="acp-stderr", daemon=True)
        self._stderr_thread.start()

    @property
    def pid(self) -> int:
        return self.popen.pid

    @property
    def stdin(self):
        return self.popen.stdin

    @property
    def stdout(self):
        return self.popen.stdout

    def _drain_stderr(self) -> None:
        try:
            for raw in self.popen.stderr:
                if self.on_stderr_line:
                    try:
                        self.on_stderr_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
                    except Exception:
                        logger.exception("ACP stderr listener failed.")
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        """Stop the agent and its descendants. Blocking; call from a worker thread."""
        from agent.modules.tools import kill_process_tree

        # Closing stdin alone does not end agy_acp_server, so the grace period
        # only covers agents that do exit on EOF.
        try:
            self.popen.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.popen.wait(_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        # Descendants can outlive the root, so the tree is always killed.
        kill_process_tree(self.popen)
        try:
            self.popen.wait(5)
        except subprocess.TimeoutExpired:
            logger.warning("ACP agent process %s did not exit after kill.", self.popen.pid)
        containment = getattr(self.popen, "coding_job", None)
        if containment:
            containment.close()
        self._stderr_thread.join(timeout=2)
        for stream in (self.popen.stdout, self.popen.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
