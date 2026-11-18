"""Run dashboard browser setup outside the HTTP request and event loop."""

import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Literal
from uuid import uuid4

from agent.shared.infrastructure.browser import find_browser
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

BrowserSetupAction = Literal["install", "repair", "check"]


class BrowserSetupBusyError(RuntimeError):
    """A browser setup operation is already running."""


class BrowserSetupManager:
    def __init__(self):
        self._lock = Lock()
        self._job: dict[str, Any] | None = None

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._job) if self._job else None

    def start(self, action: BrowserSetupAction, executable_path: str = "") -> dict[str, Any]:
        if action not in {"install", "repair", "check"}:
            raise ValueError("Unsupported browser setup action.")
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise BrowserSetupBusyError("Another browser setup operation is already running.")
            self._job = {
                "id": uuid4().hex, "action": action, "state": "running",
                "started_at": time.time(), "finished_at": None,
                "configured_executable_path": executable_path, "output": "", "error": None,
            }
            job = dict(self._job)
            try:
                Thread(target=self._run, args=(action, executable_path), name="k41-browser-setup", daemon=True).start()
            except Exception as exc:
                self._job.update(state="failed", finished_at=time.time(), error=str(exc))
                raise
            return job

    def _run(self, action: BrowserSetupAction, executable_path: str) -> None:
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe" and executable.with_name("python.exe").exists():
            executable = executable.with_name("python.exe")
        base = [str(executable), "-m", "agent.bootstrap.cli", "browser"]
        output = ""
        error = None
        checked: dict[str, str | None] = {}

        def run(command: list[str]) -> int:
            nonlocal output
            result = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=1800,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                **hidden_subprocess_kwargs(),
            )
            output = (output + (result.stdout or ""))[-32768:]
            return result.returncode

        try:
            exit_code = 0
            if action != "check":
                command = [*base, "install"]
                if action == "repair":
                    command.append("--force")
                exit_code = run(command)
            if not exit_code:
                # Installation can change discovery, and the runtime may prefer a different binary.
                target = executable_path or find_browser()
                checked["checked_executable_path"] = target
                exit_code = run([*base, "check", "--executable-path", target or ""])
            if exit_code:
                error = f"Browser {action} failed (exit {exit_code}). See the operation output below."
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or b""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            output = (output + partial)[-32768:]
            error = "Browser setup timed out. Check package-manager locks and server logs before retrying."
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        with self._lock:
            self._job.update(
                state="failed" if error else "succeeded", output=output,
                error=error, finished_at=time.time(), **checked,
            )
