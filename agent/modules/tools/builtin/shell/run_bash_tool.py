"""Stateless shell tool ``run_bash`` — isolated counterpart to persistent ``bash``.

Each invocation spawns a fresh subprocess and does not share state (cwd, env
mutations, background jobs) with any other call.  Use it to benchmark / verify
whether a failure comes from persistent-session contamination (e.g. a prior
long command leaving the shell in an unterminated quote / heredoc) vs. a real
command error.
"""

import asyncio
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from agent.modules.tools.builtin.workspace import get_workspace
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.context import ToolContext
from agent.modules.tools.runtime.sandbox import build_safe_env
from agent.modules.tools.runtime.shell_guard import check_command_blocked
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

# Reuse same limits as persistent session manager so comparison is fair.
MAX_OUTPUT_CHARS = 100_000
HARD_MAX_OUTPUT_CHARS = 200_000
HARD_MAX_TIMEOUT = 300.0
HARD_MIN_TIMEOUT = 1.0

# ANSI escape stripping (parity with session_manager.py)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


# Threshold for switching from inline args to temp-file execution to avoid OS
# ARG_MAX / CreateProcess limit (~32k on Windows). 8k is conservative and
# keeps argv small while still covering typical LLM-generated commands.
_LONG_COMMAND_THRESHOLD = 8000


def _clamp_timeout(value: float) -> float:
    try:
        v = float(value)
    except Exception:
        return 30.0
    return max(HARD_MIN_TIMEOUT, min(v, HARD_MAX_TIMEOUT))


def _truncate_output(text: str) -> tuple[str, bool]:
    """Truncate to MAX_OUTPUT_CHARS keeping the tail. Returns (text, truncated)."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    truncated_chars = len(text) - MAX_OUTPUT_CHARS
    return f"[...truncated {truncated_chars} characters...]\n{text[-MAX_OUTPUT_CHARS:]}", True


class RunBashInput(BaseModel):
    """Input schema for stateless bash execution."""

    command: str = Field(
        description=(
            "Shell command to execute in a fresh, isolated subprocess. "
            "No state is preserved between calls: cd, env vars, background jobs "
            "do not persist. For comparison, persistent `bash` keeps state per "
            "session_id within the same thread."
        ),
    )
    timeout: float = Field(
        default=30.0,
        ge=HARD_MIN_TIMEOUT,
        le=HARD_MAX_TIMEOUT,
        description="Maximum wait time in seconds for command completion. Clamped to 1-300s.",
    )


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
        ToolCapability.REQUIRES_WORKSPACE,
    ],
    tags=["shell", "stateless"],
)
@tool(args_schema=RunBashInput)
async def run_bash(
    command: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    timeout: float = 30.0,
) -> str:
    """Execute a shell command in an isolated, stateless subprocess.

    Unlike ``bash`` (persistent session with ``session_id``), this tool spawns
    a brand-new shell for every call. That means:

    * ``cd`` / ``export`` / shell functions do NOT leak to the next call.
    * A previous long / unterminated command cannot contaminate the next one.
    * No background-process tracking — the subprocess is reaped after ``timeout``.

    Use it side-by-side with ``bash`` to isolate persistent-session bugs (e.g.
    sentinel timeout with empty STDOUT caused by an unclosed quote in the
    previous command).

    Args:
        command: Shell command to run.
        timeout: Seconds to wait before killing the subprocess (1-300, default 30).
    """
    try:
        timeout = _clamp_timeout(timeout)
        workspace = get_workspace(runtime)

        # Guard dangerous commands early for local backend (same policy as bash).
        if workspace.backend == "local":
            blocked, reason = check_command_blocked(command)
            if blocked:
                raise ValueError(
                    f"Blocked dangerous command ({reason}): command rejected for local execution"
                )
            return await _run_local(command, workspace.locator, timeout)

        # Remote backends (daytona / modal) — delegate to workspace executor which
        # is already stateless per call.
        return await _run_remote(command, workspace, runtime, timeout)

    except ToolError:
        raise
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, str(exc)) from exc


async def _run_local(command: str, working_dir: str, timeout: float) -> str:
    """Run command locally in a one-shot subprocess."""

    def _execute() -> dict[str, Any]:
        # Force UTF-8 for child processes to avoid charmap errors on Windows
        # with Vietnamese characters (e.g. \u1ecd). PYTHONIOENCODING/PYTHONUTF8
        # are not in the default whitelist, so inject via extra_vars.
        safe_env = build_safe_env(
            extra_vars={
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )

        # Pick shell explicitly so behaviour matches persistent manager's choice
        # but in non-interactive mode (no -i / -NoExit). For very long commands
        # we write to a temp file to avoid OS argv limits.
        shell_args: list[str] | None = None
        use_shell_flag = False
        system = platform.system()
        temp_script: Path | None = None
        is_long = len(command) > _LONG_COMMAND_THRESHOLD
        if system == "Windows":
            if shutil.which("pwsh.exe") or shutil.which("pwsh"):
                if is_long:
                    fd, script_path = tempfile.mkstemp(suffix=".ps1", text=True)
                    temp_script = Path(script_path)
                    with open(fd, "w", encoding="utf-8", newline="\n") as f:
                        f.write(command)
                    shell_args = ["pwsh.exe", "-NoLogo", "-NoProfile", "-File", str(temp_script)]
                else:
                    shell_args = ["pwsh.exe", "-NoLogo", "-NoProfile", "-Command", command]
            elif shutil.which("powershell.exe") or shutil.which("powershell"):
                if is_long:
                    fd, script_path = tempfile.mkstemp(suffix=".ps1", text=True)
                    temp_script = Path(script_path)
                    with open(fd, "w", encoding="utf-8", newline="\n") as f:
                        f.write(command)
                    shell_args = ["powershell.exe", "-NoLogo", "-NoProfile", "-File", str(temp_script)]
                else:
                    shell_args = ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command]
            else:
                if is_long:
                    fd, script_path = tempfile.mkstemp(suffix=".bat", text=True)
                    temp_script = Path(script_path)
                    with open(fd, "w", encoding="utf-8", newline="\n") as f:
                        f.write(command)
                    shell_args = ["cmd.exe", "/c", str(temp_script)]
                else:
                    use_shell_flag = True
        else:
            if shutil.which("bash"):
                if is_long:
                    fd, script_path = tempfile.mkstemp(suffix=".sh", text=True)
                    temp_script = Path(script_path)
                    with open(fd, "w", encoding="utf-8", newline="\n") as f:
                        f.write(command)
                    shell_args = ["bash", str(temp_script)]
                else:
                    shell_args = ["bash", "-c", command]
            else:
                if is_long:
                    fd, script_path = tempfile.mkstemp(suffix=".sh", text=True)
                    temp_script = Path(script_path)
                    with open(fd, "w", encoding="utf-8", newline="\n") as f:
                        f.write(command)
                    shell_args = ["sh", str(temp_script)]
                else:
                    shell_args = ["sh", "-c", command]

        creationflags = 0
        if system == "Windows":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            if use_shell_flag:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=working_dir,
                    env=safe_env,
                    timeout=timeout,
                    **hidden_subprocess_kwargs(creationflags=creationflags),
                )
            else:
                assert shell_args is not None
                result = subprocess.run(
                    shell_args,
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=working_dir,
                    env=safe_env,
                    timeout=timeout,
                    **hidden_subprocess_kwargs(creationflags=creationflags),
                )
            return {
                "stdout": _strip_ansi(result.stdout or ""),
                "stderr": _strip_ansi(result.stderr or ""),
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))  # type: ignore[union-attr]
            stderr = (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))  # type: ignore[union-attr]
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return {
                "stdout": _strip_ansi(stdout),
                "stderr": _strip_ansi(stderr),
                "exit_code": None,
                "timed_out": True,
            }
        except FileNotFoundError as exc:
            return {"error": f"Working directory not found: {working_dir}: {exc}"}
        except OSError as exc:
            return {"error": str(exc)}
        finally:
            if temp_script is not None:
                try:
                    temp_script.unlink(missing_ok=True)
                except Exception:
                    pass

    raw: dict[str, Any] = await asyncio.to_thread(_execute)

    if "error" in raw:
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, str(raw["error"]))

    stdout: str = raw.get("stdout", "")
    stderr: str = raw.get("stderr", "")
    exit_code = raw.get("exit_code")
    timed_out = bool(raw.get("timed_out"))

    # Truncate to keep parity with bash's MAX_OUTPUT_CHARS
    stdout_trunc, out_truncated = _truncate_output(stdout)
    stderr_trunc, err_truncated = _truncate_output(stderr)
    truncated = out_truncated or err_truncated

    result_str = f"STDOUT:\n{stdout_trunc}"
    if stderr_trunc:
        result_str += f"\nSTDERR:\n{stderr_trunc}"
    if timed_out:
        result_str += f"\n[TIMEOUT after {timeout:.1f}s — process killed]"
    elif exit_code is not None and exit_code != 0:
        result_str += f"\nExit code: {exit_code}"
    if truncated:
        result_str += "\n[Output truncated to 100k chars — tail kept]"
    return result_str


async def _run_remote(
    command: str,
    workspace: Any,
    runtime: ToolRuntime[Any, Any],
    timeout: float,
) -> str:
    """Delegate to workspace command executor (daytona/modal)."""
    from agent.modules.workspaces import get_workspace_command_executor

    # Resolve thread_id for executor scoping
    try:
        ctx = ToolContext.from_runtime(runtime)
        thread_id = ctx.thread_id
    except Exception:
        thread_id = None

    executor = await get_workspace_command_executor(workspace, thread_id=thread_id)
    # Workspace executor expects int timeout
    result = await executor.execute(
        command,
        timeout=int(timeout),
        max_output_chars=MAX_OUTPUT_CHARS,
    )
    output = getattr(result, "output", "") or ""
    exit_code = getattr(result, "exit_code", None)
    truncated = bool(getattr(result, "truncated", False))

    out_trunc, _ = _truncate_output(output)

    # Workspace executor merges stdout+stderr into output; keep format compatible
    result_str = f"STDOUT:\n{out_trunc}"
    if exit_code is not None and exit_code != 0:
        result_str += f"\nExit code: {exit_code}"
    if truncated:
        result_str += "\n[Output truncated to 100k chars — tail kept]"
    return result_str
