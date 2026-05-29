"""Terminal session manager for managing persistent shell processes."""

from __future__ import annotations

import logging
import os
import re
import platform
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Dict, List, Optional

from agent.modules.tools.runtime.sandbox import build_safe_env

logger = logging.getLogger(__name__)

# Limits
MAX_OUTPUT_CHARS = 100_000  # ~100KB max output per command
MAX_HISTORY_LINES = 500  # Keep last 500 lines of drained output

# ANSI escape code pattern (colors, cursor movement, etc.)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_ESCAPE_RE.sub("", text)


@dataclass
class TerminalSession:
    session_id: str
    working_dir: str
    process: subprocess.Popen
    output_queue: Queue[str]
    error_queue: Queue[str]
    is_running: bool = True
    has_background_process: bool = False
    output_history: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_LINES))


class TerminalSessionManager:
    """Manages persistent terminal sessions (processes) with interactive capabilities."""

    def __init__(self) -> None:
        self.sessions: Dict[str, TerminalSession] = {}

    def create_session(self, working_dir: str, session_name: Optional[str] = None) -> str:
        """Create a new terminal session with a specific working directory."""
        session_id = session_name or f"term_{uuid.uuid4().hex[:8]}"

        # Determine the appropriate shell command with optimized config
        if platform.system() == "Windows":
            if shutil.which("pwsh.exe") or shutil.which("pwsh"):
                shell_cmd = ["pwsh.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"]
            elif shutil.which("powershell.exe") or shutil.which("powershell"):
                shell_cmd = ["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"]
            else:
                shell_cmd = ["cmd.exe", "/Q", "/K"]
        else:
            if shutil.which("bash"):
                shell_cmd = ["bash", "-i"]
            else:
                shell_cmd = ["sh", "-i"]

        safe_env = build_safe_env()
        
        # Win32 creation flag for process isolation
        creationflags = 0
        if platform.system() == "Windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        process = subprocess.Popen(
            shell_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=working_dir,
            creationflags=creationflags,
            encoding="utf-8",
            errors="replace",
            env=safe_env,
        )

        # Register the PID in the active session registry
        from agent.modules.agent_runtime.active_sessions import current_session_id_var, get_active_session_registry
        session_id_context = current_session_id_var.get()
        if session_id_context:
            registry = get_active_session_registry()
            registry.register_pid(session_id_context, process.pid)

        output_queue: Queue[str] = Queue()
        error_queue: Queue[str] = Queue()

        def read_output(pipe: Any, queue: Queue[str]) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    if line:
                        queue.put(_strip_ansi(line.rstrip()))
            except Exception as e:
                queue.put(f"Error reading output: {str(e)}")

        threading.Thread(
            target=read_output, args=(process.stdout, output_queue), daemon=True
        ).start()

        threading.Thread(
            target=read_output, args=(process.stderr, error_queue), daemon=True
        ).start()

        session = TerminalSession(
            session_id=session_id,
            working_dir=working_dir,
            process=process,
            output_queue=output_queue,
            error_queue=error_queue,
        )

        self.sessions[session_id] = session

        # Wait a bit for terminal to initialize
        time.sleep(0.2)

        # Configure UTF-8 for Windows shells immediately to avoid font issues
        if platform.system() == "Windows" and process.stdin:
            try:
                # Detect shell type from path
                is_powershell = "powershell" in shell_cmd[0].lower() or "pwsh" in shell_cmd[0].lower()
                if is_powershell:
                    process.stdin.write(
                        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                        "$PSStyle.OutputRendering = 'PlainText'; "
                        "chcp 65001\n"
                    )
                else:
                    process.stdin.write("chcp 65001\n")
                process.stdin.flush()
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Failed to configure terminal encoding to UTF-8: {e}")

        # Clear initialization messages
        self._drain_queues_to_history(session)

        return session_id

    def _drain_queues_to_history(self, session: TerminalSession) -> None:
        """Move pending queue items to history buffer instead of discarding them."""
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
        """Truncate output if it exceeds MAX_OUTPUT_CHARS, keeping the tail."""
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        truncated_chars = len(text) - MAX_OUTPUT_CHARS
        return f"[...truncated {truncated_chars} characters...]\n{text[-MAX_OUTPUT_CHARS:]}"

    def execute_command(
        self,
        session_id: str,
        command: str,
        timeout: float = 30.0,
        run_in_background: bool = False,
        working_dir: str | None = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Execute a command in a session, auto-creating the session if it doesn't exist."""
        if session_id not in self.sessions:
            if working_dir is None:
                return {"error": f"Session {session_id} does not exist and no working_dir provided"}
            self.create_session(working_dir, session_id)

        session = self.sessions[session_id]

        if not session.is_running or session.process.poll() is not None:
            return {"error": f"Session {session_id} is no longer active"}

        # Drain stale outputs to history before executing a new command
        self._drain_queues_to_history(session)

        # Dynamic check if session has active child processes
        import psutil
        try:
            parent = psutil.Process(session.process.pid)
            active_children = parent.children(recursive=True)
            has_active_children = len(active_children) > 0
        except Exception:
            has_active_children = False

        # Warn if there is already an active background process
        if has_active_children and not force:
            session.has_background_process = True
            return {
                "warning": (
                    f"Session '{session_id}' has a background process running. "
                    f"Use force=True to execute anyway, or use bash_interrupt to stop it, "
                    f"or create a new session."
                ),
                "session_id": session_id,
            }
        elif not has_active_children:
            session.has_background_process = False

        # Generate a unique sentinel token for synchronous execution
        sentinel_id = uuid.uuid4().hex[:8]
        sentinel_token = f"____CMD_DONE_{sentinel_id}____"

        try:
            # Send command
            if session.process.stdin:
                if run_in_background:
                    session.process.stdin.write(command + "\n")
                    session.process.stdin.flush()
                    # Mark session as having background process
                    session.has_background_process = True
                    return {
                        "session_id": session_id,
                        "command": command,
                        "status": "running_background",
                    }
                else:
                    # For synchronous commands, send the command followed by the sentinel echo
                    full_command = f"{command}\necho {sentinel_token}\n"
                    session.process.stdin.write(full_command)
                    session.process.stdin.flush()

            output_lines = []
            error_lines = []
            total_output_chars = 0

            start_time = time.time()
            last_output_time = start_time
            idle_timeout = 30.0  # Timeout when no new output

            while time.time() - start_time < timeout:
                # Fast exit if process has terminated
                if session.process.poll() is not None:
                    break

                got_output = False

                try:
                    # Quick block to receive outputs responsively without high CPU load
                    line = session.output_queue.get(timeout=0.05)
                    if sentinel_token in line:
                        break  # Sentinel token found, execution is complete
                    
                    # Filter out Windows code page switch messages
                    if "Active code page:" in line:
                        continue
                    
                    # Track total output size for truncation
                    total_output_chars += len(line)
                    if total_output_chars <= MAX_OUTPUT_CHARS:
                        output_lines.append(line)
                    elif not output_lines or output_lines[-1] != "[...output truncated...]":
                        output_lines.append("[...output truncated...]")

                    last_output_time = time.time()
                    got_output = True
                except Empty:
                    pass

                try:
                    err = session.error_queue.get_nowait()
                    # Filter out unnecessary PSReadline messages
                    if "PSReadline" not in err:
                        error_lines.append(err)
                    last_output_time = time.time()
                    got_output = True
                except Empty:
                    pass

                # Idle timeout: only effective when timeout is large enough
                if not got_output and timeout >= idle_timeout and (time.time() - last_output_time) > idle_timeout:
                    break

            output_text = self._truncate_output("\n".join(output_lines))

            return {
                "session_id": session_id,
                "command": command,
                "output": output_text,
                "stderr": "\n".join(error_lines),
                "status": "completed",
            }

        except Exception as e:
            return {"error": str(e)}

    def get_session_output(
        self, session_id: str, timeout: float = 1.0
    ) -> Dict[str, Any]:
        """Get output from background process running in a session."""
        if session_id not in self.sessions:
            return {"error": f"Session {session_id} does not exist"}

        session = self.sessions[session_id]
        output_lines = []
        error_lines = []
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
                err = session.error_queue.get_nowait()
                if "PSReadline" not in err:
                    error_lines.append(err)
            except Empty:
                pass

            if (
                not session.is_running
                and session.output_queue.empty()
                and session.error_queue.empty()
            ):
                break

        output_text = self._truncate_output("\n".join(output_lines))

        return {
            "session_id": session_id,
            "output": output_text,
            "stderr": "\n".join(error_lines),
            "is_running": session.process.poll() is None,
        }

    def send_input(self, session_id: str, text: str) -> Dict[str, Any]:
        """Send text input (stdin) to a running session.

        Args:
            session_id: The session to send input to.
            text: The text to write to stdin. A newline is appended automatically.

        Returns:
            Dict with confirmation or error.
        """
        if session_id not in self.sessions:
            return {"error": f"Session '{session_id}' does not exist"}

        session = self.sessions[session_id]

        if session.process.poll() is not None:
            return {"error": f"Session '{session_id}' process has terminated"}

        try:
            if session.process.stdin:
                session.process.stdin.write(text + "\n")
                session.process.stdin.flush()
                return {"status": "sent", "session_id": session_id, "text": text}
            else:
                return {"error": f"Session '{session_id}' stdin is not available"}
        except Exception as e:
            return {"error": f"Failed to send input: {str(e)}"}

    def send_signal(self, session_id: str, signal_type: str = "interrupt") -> Dict[str, Any]:
        """Send a signal to the active process in a session.

        Args:
            session_id: The session to signal.
            signal_type: 'interrupt' (Ctrl+C / SIGINT) or 'terminate' (SIGTERM).

        Returns:
            Dict with confirmation or error.
        """
        if session_id not in self.sessions:
            return {"error": f"Session '{session_id}' does not exist"}

        session = self.sessions[session_id]

        if session.process.poll() is not None:
            return {"error": f"Session '{session_id}' process has already terminated"}

        try:
            import psutil

            # Find the deepest child process to signal
            try:
                parent = psutil.Process(session.process.pid)
                children = parent.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []

            # Target: the deepest child, or the shell process itself
            target_process = children[-1] if children else session.process

            if signal_type == "interrupt":
                if platform.system() == "Windows":
                    # On Windows, send CTRL_C_EVENT to process group
                    try:
                        os.kill(session.process.pid, signal.CTRL_C_EVENT)
                    except Exception:
                        # Fallback: kill the child process directly
                        if children:
                            children[-1].send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    target_process.send_signal(signal.SIGINT)
            elif signal_type == "terminate":
                if platform.system() == "Windows":
                    target_process.terminate()
                else:
                    target_process.send_signal(signal.SIGTERM)
            else:
                return {"error": f"Unknown signal_type: '{signal_type}'. Use 'interrupt' or 'terminate'."}

            return {
                "status": "signal_sent",
                "session_id": session_id,
                "signal_type": signal_type,
                "target_pid": target_process.pid if hasattr(target_process, "pid") else "unknown",
            }

        except Exception as e:
            return {"error": f"Failed to send signal: {str(e)}"}

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions."""
        return [
            {
                "session_id": sid,
                "working_dir": s.working_dir,
                "is_running": s.process.poll() is None,
            }
            for sid, s in self.sessions.items()
        ]

    def close_session(self, session_id: str) -> bool:
        """Close terminal session and kill all child processes."""
        if session_id not in self.sessions:
            return False

        session = self.sessions[session_id]
        session.is_running = False

        # Unregister the PID
        from agent.modules.agent_runtime.active_sessions import current_session_id_var, get_active_session_registry
        session_id_context = current_session_id_var.get()
        if session_id_context:
            registry = get_active_session_registry()
            registry.unregister_pid(session_id_context, session.process.pid)

        try:
            # Send exit command
            if session.process.stdin:
                session.process.stdin.write("exit\n")
                session.process.stdin.flush()
                session.process.stdin.close()
            session.process.wait(timeout=3)
        except Exception:
            pass

        # Kill all processes in process group
        try:
            import psutil

            # Get all child processes
            try:
                parent = psutil.Process(session.process.pid)
                children = parent.children(recursive=True)

                # Kill each child process
                for child in children:
                    try:
                        child.kill()
                    except Exception:
                        pass

                # Wait for processes to terminate
                gone, alive = psutil.wait_procs(children, timeout=3)

                # Force kill if still alive
                for p in alive:
                    try:
                        p.kill()
                    except Exception:
                        pass
            except psutil.NoSuchProcess:
                pass

            # Kill main process
            try:
                session.process.terminate()
                session.process.wait(timeout=2)
            except Exception:
                session.process.kill()

        except Exception:
            # Fallback: use taskkill on Windows or kill on Unix
            try:
                if platform.system() == "Windows":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(session.process.pid)],
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    subprocess.run(
                        ["kill", "-9", str(session.process.pid)],
                        capture_output=True,
                        timeout=5,
                    )
            except Exception:
                pass

        if session_id in self.sessions:
            del self.sessions[session_id]
        return True

    def close_all_sessions(self) -> None:
        """Close all sessions."""
        for session_id in list(self.sessions.keys()):
            self.close_session(session_id)


# Global singleton instance
session_manager = TerminalSessionManager()
