"""Stateless shell tool ``run_bash``.

Each invocation spawns a fresh, isolated subprocess: no shell state
(cwd, env mutations, background jobs) is shared between calls. Use it
side-by-side with the persistent ``bash`` session tool to isolate
persistent-session contamination.

Behavior:

- Input: ``command`` (required), ``workdir`` (optional, relative paths
  resolve from the active workspace), ``timeout`` in seconds (optional,
  default 120, clamped to 1-600).
- Best-effort advisory warnings when command arguments reference absolute
  directories outside the working directory.
- Combined stdout/stderr capture with a ``(no output)`` fallback and an
  bounded model preview and owner-scoped retained output.
- Result footer: ``Command exited with code <exit>.`` or
  ``Command timed out before completion.`` prefixed by a ``Warnings:``
  block when advisory warnings exist.
- Shell selection from ``K41_SHELL`` with a platform default fallback.
- Timeout enforcement kills the whole process tree and drains pipes with
  a bounded grace period, so orphaned grandchildren cannot hold the call
  past its timeout.
- Local execution uses the shared coding process manager.
- Dangerous commands are rejected via ``shell_guard``; the child process
  runs with the ``build_safe_env`` whitelist plus UTF-8 injection.
"""

import asyncio
import os
import platform
import re
import shlex
import shutil
import tempfile
from pathlib import PureWindowsPath
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime
from langgraph.errors import GraphInterrupt
from pydantic import BaseModel, Field

from agent.modules.tools.builtin.workspace import get_workspace
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.context import ToolContext
from agent.modules.tools.runtime.shell_guard import check_command_blocked

# Safety limits. Timeout bounds are stored in milliseconds and exposed
# to the model in seconds (default 120s, max 600s).
DEFAULT_TIMEOUT_MS = 2 * 60 * 1_000
MAX_TIMEOUT_MS = 10 * 60 * 1_000
MAX_CAPTURE_BYTES = 1024 * 1024

DEFAULT_TIMEOUT = DEFAULT_TIMEOUT_MS / 1_000
HARD_MAX_TIMEOUT = MAX_TIMEOUT_MS / 1_000
HARD_MIN_TIMEOUT = 1.0

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_SHELL_TOKEN_RE = re.compile(r"(?:[^\s\"']+|\"[^\"]*\"|'[^']*')+")
_TRAILING_CHAIN_RE = re.compile(r"[;,|&]+$")
_UTF8_CONTINUATION_MASK = 0xC0
_UTF8_CONTINUATION_SIG = 0x80

_TRUNCATION_NOTICE = "[output capture truncated at the in-memory safety limit]"


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _clamp_timeout(value: float) -> float:
    try:
        v = float(value)
    except Exception:
        return DEFAULT_TIMEOUT
    return max(HARD_MIN_TIMEOUT, min(v, HARD_MAX_TIMEOUT))


def _default_shell() -> str:
    # Platform default: COMSPEC (usually cmd.exe) on Windows, /bin/sh elsewhere.
    if platform.system() == "Windows":
        return os.environ.get("COMSPEC", "cmd.exe") or "cmd.exe"
    return "/bin/sh"


def _resolve_shell() -> str:
    # K41_SHELL wins when set; otherwise auto-detect the best available
    # shell, falling back to the platform default.
    override = (os.environ.get("K41_SHELL") or "").strip()
    if override:
        return override
    system = platform.system()
    if system == "Windows":
        if shutil.which("pwsh.exe") or shutil.which("pwsh"):
            return "pwsh.exe"
        if shutil.which("powershell.exe") or shutil.which("powershell"):
            return "powershell.exe"
        return _default_shell()
    if shutil.which("bash"):
        return "bash"
    if shutil.which("sh"):
        return "sh"
    return _default_shell()


def _shell_tokens(command: str) -> list[str]:
    # Split a command string into tokens, keeping quoted segments intact.
    return _SHELL_TOKEN_RE.findall(command) or []


def _unquote(value: str) -> str:
    # Strip one layer of matching surrounding quotes.
    if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
        return value[1:-1]
    return value


def _contains(outer: str, inner: str) -> bool:
    # True when canonical path ``inner`` is inside directory ``outer``.
    try:
        real_outer = os.path.normcase(os.path.realpath(outer))
        real_inner = os.path.normcase(os.path.realpath(inner))
        return os.path.commonpath([real_outer, real_inner]) == real_outer
    except (OSError, ValueError):
        return False


def _is_absolute_token(value: str) -> bool:
    # Cross-platform absolute-path check for advisory scanning: a POSIX
    # absolute path is still meaningful inside git-bash shells on Windows
    # and vice versa, while os.path.isabs only covers the host platform.
    if os.path.isabs(value) or value.startswith("/"):
        return True
    try:
        return PureWindowsPath(value).is_absolute()
    except Exception:
        return False


def _external_command_directories(command: str, cwd: str) -> list[str]:
    # Token-based, best-effort, advisory-only scan for absolute paths in
    # the command that point outside ``cwd``.
    directories: set[str] = set()
    for token in _shell_tokens(command):
        value = _TRAILING_CHAIN_RE.sub("", _unquote(token))
        if not value or not _is_absolute_token(value):
            continue
        try:
            resolved = os.path.realpath(value)
        except OSError:
            continue
        if _contains(cwd, resolved):
            continue
        try:
            directories.add(os.path.realpath(os.path.dirname(resolved)))
        except OSError:
            continue
    return sorted(directories)


def _format_external_warning(directory: str) -> str:
    # Advisory notice shown to the model; never blocks execution.
    display = os.path.join(directory, "*").replace("\\", "/")
    return (
        f"Command argument references external directory {display}. "
        "Bash runs with host-user filesystem, process, and network "
        "authority; this scan is advisory only."
    )


def _resolve_workdir(workdir: str | None, workspace_locator: str) -> str:
    # Relative workdir values resolve from the active workspace; the result
    # is a canonical absolute path.
    base = os.path.realpath(os.path.expanduser(workspace_locator))
    if workdir is None or str(workdir).strip() in ("", "."):
        return base
    candidate = os.path.expanduser(str(workdir).strip())
    joined = candidate if os.path.isabs(candidate) else os.path.join(base, candidate)
    return os.path.realpath(joined)


def _workdir_warning(target: str, workspace_locator: str) -> str | None:
    try:
        base = os.path.realpath(os.path.expanduser(workspace_locator))
        temp_root = os.path.realpath(tempfile.gettempdir())
    except OSError:
        return None
    if _contains(base, target) or _contains(temp_root, target):
        return None
    display = os.path.join(target, "*").replace("\\", "/")
    return (
        f"Workdir references external directory {display}. "
        "run_bash runs with host-user filesystem, process, and network "
        "authority; this scan is advisory only."
    )


def _truncate_to_capture_limit(text: str) -> tuple[str, bool]:
    # Keep the tail within MAX_CAPTURE_BYTES (split-safe for utf-8) and
    # report capture loss with a notice.
    data = text.encode("utf-8", errors="replace")
    if len(data) <= MAX_CAPTURE_BYTES:
        return text, False
    start = len(data) - MAX_CAPTURE_BYTES
    while start < len(data) and (data[start] & _UTF8_CONTINUATION_MASK) == _UTF8_CONTINUATION_SIG:
        start += 1
    tail = data[start:].decode("utf-8", errors="replace")
    return f"{tail}\n\n{_TRUNCATION_NOTICE}", True


def _timeout_message(timeout_ms: int) -> str:
    # Message returned as the command output when the timeout expires.
    return (
        f"Command exceeded timeout of {timeout_ms} ms. "
        "Retry with a larger timeout if the command is expected to take longer."
    )


def _model_footer(exit_code: int | None, timed_out: bool, warnings: list[str]) -> str:
    # Footer appended to every result: warnings first, then the exit or
    # timeout line.
    prefix = ""
    if warnings:
        prefix = "Warnings:\n" + "\n".join(f"- {warning}" for warning in warnings) + "\n\n"
    if timed_out:
        return f"{prefix}Command timed out before completion."
    if exit_code is None:
        return f"{prefix}Command exited with unknown code."
    return f"{prefix}Command exited with code {exit_code}."


def _combine_streams(stdout: str, stderr: str) -> str:
    # Merge both pipes into a single stream. Exact interleaving is
    # unavailable with separate pipes, so stdout comes first with stderr
    # appended when present.
    stdout = stdout or ""
    stderr = stderr or ""
    if stdout and stderr:
        return f"{stdout}\n{stderr}" if not stdout.endswith("\n") else f"{stdout}{stderr}"
    return stdout or stderr


def _coerce_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class RunBashInput(BaseModel):
    """Input schema for stateless shell execution."""

    command: str = Field(
        description=(
            "Shell command string to execute in a fresh, isolated subprocess. "
            "No state is preserved between calls: cd, env vars, background jobs "
            "do not persist. Use workdir instead of 'cd <directory> && <command>'. "
            "Prefer dedicated file tools (Glob/Grep/Read/Edit/Write) over "
            "find/grep/cat/head/tail/sed/awk/echo; use this tool for terminal "
            "operations like git, npm, docker."
        ),
    )
    workdir: str | None = Field(
        default=None,
        description=(
            "Working directory. Defaults to the active workspace; relative paths "
            "resolve from that workspace. Use this instead of 'cd' commands. "
            "Local paths outside the workspace require permission approval."
        ),
    )
    timeout: float = Field(
        default=DEFAULT_TIMEOUT,
        ge=HARD_MIN_TIMEOUT,
        le=HARD_MAX_TIMEOUT,
        description=(
            "Maximum wait time in seconds for command completion. "
            "Defaults to 120s, clamped to 1-600s."
        ),
    )


@tool(args_schema=RunBashInput)
async def run_bash(
    command: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    workdir: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Execute one shell command string with host-user authority, statelessly.

    The active workspace is the default working directory. Relative workdir
    values resolve from that workspace. External workdir values and absolute
    command-argument paths outside the working directory produce advisory
    warnings. Local external workdirs require permission approval.
    Timeout is in seconds (default 120, max 600). Uses
    K41_SHELL when set; otherwise uses pwsh/powershell/cmd on Windows and
    bash/sh on POSIX.

    Each invocation spawns a fresh subprocess and does not share state (cwd,
    env mutations, background jobs) with any other call. Use it for terminal
    operations like git, npm, docker — not for file reads/writes/searches.
    Use an approved directory for temporary work outside the workspace.
    Only commit, amend,
    push, or create PRs when explicitly requested; inspect status/diff/log
    before committing and never commit secrets.

    Args:
        command: Shell command string to run.
        workdir: Working directory for the command. Defaults to workspace.
        timeout: Seconds to wait before killing the subprocess (1-600).
    """
    try:
        if not command or not str(command).strip():
            raise ValueError("Command must be a non-empty string")
        timeout_s = _clamp_timeout(timeout)
        timeout_ms = round(timeout_s * 1_000)
        workspace = get_workspace(runtime)
        backend = str(getattr(workspace, "backend", "local") or "local")

        if backend != "local":
            warnings: list[str] = []
            try:
                metadata = getattr(workspace, "metadata", None) or {}
                root = str(metadata.get("root") or "").strip() if isinstance(metadata, dict) else ""
                if root:
                    warnings = [_format_external_warning(d) for d in _external_command_directories(command, root)]
            except Exception:
                warnings = []
            return await _run_remote(command, workspace, runtime, timeout_s, timeout_ms, warnings, workdir)

        locator = str(getattr(workspace, "locator", "") or "").strip()
        if not locator:
            raise ValueError("Workspace locator is not configured")
        target = _resolve_workdir(workdir, locator)
        if not os.path.isdir(target):
            raise ValueError(f"Working directory is not a directory: {target}")
        warnings = [_format_external_warning(d) for d in _external_command_directories(command, target)]
        extra = _workdir_warning(target, locator)
        if extra:
            warnings.append(extra)

        blocked, reason = check_command_blocked(command)
        if blocked:
            raise ValueError(f"Blocked dangerous command ({reason}): command rejected for local execution")
        return await _run_local(command, target, timeout_s, timeout_ms, warnings, runtime)

    except (ToolError, GraphInterrupt):
        raise
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
    except Exception as exc:
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, str(exc)) from exc


async def _run_local(
    command: str,
    working_dir: str,
    timeout_s: float,
    timeout_ms: int,
    warnings: list[str],
    runtime: Any,
) -> str:
    """Compatibility adapter over the shared local process runtime."""
    from agent.modules.tools.coding.adapter import invocation_context, make_coding_tool
    from agent.modules.tools.coding.service import get_coding_service

    service = get_coding_service()
    context = invocation_context(runtime)
    definition = make_coding_tool("bash").coding_definition
    result = await service.invoke(definition, {
        "command": command, "workdir": working_dir,
        "timeout_seconds": timeout_s, "yield_time_ms": 30000,
    }, context)
    if result.error:
        return str(result.content)
    process_id = result.data.get("process_id")
    if process_id and result.status == "running":
        job = service.processes.get(context, process_id)
        try:
            await job.finished.wait()
            result = await service.processes.observe(job)
        except asyncio.CancelledError:
            await service.processes.stop(job)
            raise
    body = str(result.content)
    if result.output_refs:
        body += f"\nOutput reference: {result.output_refs[0]} (read_tool_output)."
    return f"{body}\n\n{_model_footer(result.data.get('exit_code'), result.data.get('state') == 'timeout', warnings)}"


async def _run_remote(
    command: str,
    workspace: Any,
    runtime: ToolRuntime[Any, Any],
    timeout_s: float,
    timeout_ms: int,
    warnings: list[str],
    workdir: str | None,
) -> str:
    """Delegate to workspace command executor (daytona/modal)."""
    from agent.modules.workspaces import get_workspace_command_executor

    try:
        ctx = ToolContext.from_runtime(runtime)
        thread_id = ctx.thread_id
    except Exception:
        thread_id = None

    effective_command = command
    extra_warnings = list(warnings)
    requested = (workdir or "").strip()
    if requested and requested not in (".", "./"):
        # Remote sandboxes have no local path; only relative workdir values
        # can be honored via cd-wrapping. Absolute local paths are noted.
        is_abs = os.path.isabs(requested) or bool(re.match(r"^[A-Za-z]:[\\/]", requested))
        if is_abs:
            display = os.path.join(requested, "*").replace("\\", "/")
            extra_warnings.append(
                f"Workdir references external directory {display}. "
                "Remote sandboxes cannot resolve local absolute paths; "
                "running in the sandbox default directory."
            )
        else:
            effective_command = f"cd {shlex.quote(requested)} && ({command})"

    executor = await get_workspace_command_executor(workspace, thread_id=thread_id)
    result = await executor.execute(
        effective_command,
        timeout=int(timeout_s),
        max_output_chars=MAX_CAPTURE_BYTES,
    )
    output = _strip_ansi(getattr(result, "output", "") or "")
    exit_code = getattr(result, "exit_code", None)
    try:
        exit_code = None if exit_code is None else int(exit_code)
    except (TypeError, ValueError):
        exit_code = None
    truncated = bool(getattr(result, "truncated", False))

    body = output.strip() or "(no output)"
    if truncated and _TRUNCATION_NOTICE not in body:
        body = f"{body}\n\n{_TRUNCATION_NOTICE}"
    # Keep remote output within the same capture bound as local.
    body, _ = _truncate_to_capture_limit(body)
    _ = timeout_ms
    return f"{body}\n\n{_model_footer(exit_code, False, extra_warnings)}"


# Keep direct internal callers compatible without catalog registration.
from agent.modules.tools.middleware import apply_default_middleware

apply_default_middleware(run_bash)
