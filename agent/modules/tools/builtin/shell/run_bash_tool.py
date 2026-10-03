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
  in-memory truncation notice.
- Result footer: ``Command exited with code <exit>.`` or
  ``Command timed out before completion.`` prefixed by a ``Warnings:``
  block when advisory warnings exist.
- Shell selection from ``K41_SHELL`` with a platform default fallback.
- Timeout enforcement kills the whole process tree and drains pipes with
  a bounded grace period, so orphaned grandchildren cannot hold the call
  past its timeout.
- Dangerous commands are rejected via ``shell_guard``; the child process
  runs with the ``build_safe_env`` whitelist plus UTF-8 injection.
"""

import asyncio
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path, PureWindowsPath
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

# Threshold for switching from inline args to temp-file execution to avoid
# OS ARG_MAX / CreateProcess limits (~32k on Windows). 8k is conservative
# and keeps argv small while still covering typical model commands.
_LONG_COMMAND_THRESHOLD = 8000

_TRUNCATION_NOTICE = "[output capture truncated at the in-memory safety limit]"

# Grace period to drain pipes after killing the tree. Must stay bounded:
# an orphaned grandchild can hold the captured pipes open indefinitely.
_POST_KILL_GRACE = 3.0


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


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort kill of a spawned shell and all its descendants.

    Fire-and-forget: never blocks. Timeout enforcement depends on this —
    killing only the direct child leaves grandchildren (e.g. a
    ``python app.py`` server) holding the captured pipes, which makes the
    post-kill pipe drain block forever.
    """
    pid = getattr(proc, "pid", None)
    if pid is None:
        try:
            proc.kill()
        except Exception:
            pass
        return
    if platform.system() == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                **hidden_subprocess_kwargs(),
            )
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return
    except Exception:
        pass
    try:
        import psutil

        try:
            root = psutil.Process(pid)
            for child in root.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
        except Exception:
            pass
    except ImportError:
        pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def _close_pipes(proc: subprocess.Popen) -> None:
    for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass


def _drain_after_kill(proc: subprocess.Popen, fallback_out: Any, fallback_err: Any) -> tuple[Any, Any]:
    """Bounded pipe drain after a timeout kill.

    Falls back to the partial output captured at timeout when orphaned
    grandchildren keep the pipes open past the grace period.
    """
    try:
        return proc.communicate(timeout=_POST_KILL_GRACE)
    except subprocess.TimeoutExpired:
        _close_pipes(proc)
        return fallback_out, fallback_err
    except Exception:
        return fallback_out, fallback_err


def _reap(proc: subprocess.Popen) -> None:
    """Best-effort, bounded reap of the direct child (zombie prevention)."""
    try:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    except Exception:
        pass


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
            "Values outside the workspace (except the system temp dir) are allowed "
            "but reported as advisory external-directory warnings."
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
    workdir: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Execute one shell command string with host-user authority, statelessly.

    The active workspace is the default working directory. Relative workdir
    values resolve from that workspace. External workdir values and absolute
    command-argument paths outside the working directory produce advisory
    warnings only. Timeout is in seconds (default 120, max 600). Uses
    K41_SHELL when set; otherwise uses pwsh/powershell/cmd on Windows and
    bash/sh on POSIX.

    Each invocation spawns a fresh subprocess and does not share state (cwd,
    env mutations, background jobs) with any other call. Use it for terminal
    operations like git, npm, docker — not for file reads/writes/searches.
    Use the system temp directory for temporary work outside the workspace;
    it is pre-approved for external directory access. Only commit, amend,
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
        return await _run_local(command, target, timeout_s, timeout_ms, warnings)

    except ToolError:
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
) -> str:
    """Run command locally in a one-shot subprocess."""

    def _execute() -> dict[str, Any]:
        # Force UTF-8 for child processes to avoid charmap errors on Windows
        # with non-ASCII output. PYTHONIOENCODING/PYTHONUTF8 are injected via
        # extra_vars because they are not in the default whitelist.
        safe_env = build_safe_env(
            extra_vars={
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        shell_name = _resolve_shell()
        shell_args: list[str] | None = None
        use_shell_flag = False
        system = platform.system()
        temp_script: Path | None = None
        is_long = len(command) > _LONG_COMMAND_THRESHOLD
        lowered = shell_name.lower()
        is_powershell = "pwsh" in lowered or "powershell" in lowered
        base_name = os.path.basename(lowered).removesuffix(".exe")
        is_cmd = base_name in {"cmd", "command"}

        def _write_temp(suffix: str) -> Path:
            fd, script_path = tempfile.mkstemp(suffix=suffix, text=True)
            temp_path = Path(script_path)
            with open(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(command)
            return temp_path

        if system == "Windows":
            if is_powershell:
                exe = shell_name
                if is_long:
                    temp_script = _write_temp(".ps1")
                    shell_args = [exe, "-NoLogo", "-NoProfile", "-File", str(temp_script)]
                else:
                    shell_args = [exe, "-NoLogo", "-NoProfile", "-Command", command]
            elif is_cmd:
                if is_long:
                    temp_script = _write_temp(".bat")
                    shell_args = ["cmd.exe", "/c", str(temp_script)]
                else:
                    # Run through the system shell (COMSPEC).
                    use_shell_flag = True
            elif shutil.which(shell_name):
                if is_long:
                    temp_script = _write_temp(".ps1" if shell_name.lower().endswith(".ps1") else ".sh")
                    shell_args = [shell_name, str(temp_script)]
                else:
                    shell_args = [shell_name, "-c", command]
            elif shutil.which("pwsh.exe") or shutil.which("pwsh"):
                if is_long:
                    temp_script = _write_temp(".ps1")
                    shell_args = ["pwsh.exe", "-NoLogo", "-NoProfile", "-File", str(temp_script)]
                else:
                    shell_args = ["pwsh.exe", "-NoLogo", "-NoProfile", "-Command", command]
            elif shutil.which("powershell.exe") or shutil.which("powershell"):
                if is_long:
                    temp_script = _write_temp(".ps1")
                    shell_args = ["powershell.exe", "-NoLogo", "-NoProfile", "-File", str(temp_script)]
                else:
                    shell_args = ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command]
            else:
                use_shell_flag = True
        else:
            if shell_name in {"bash", "/bin/bash"} and shutil.which("bash"):
                exe = "bash"
            elif shutil.which(shell_name):
                exe = shell_name
            else:
                exe = "bash" if shutil.which("bash") else "sh"
            if is_long:
                temp_script = _write_temp(".sh")
                shell_args = [exe, str(temp_script)]
            else:
                shell_args = [exe, "-c", command]

        creationflags = 0
        start_new_session = False
        if system == "Windows":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            # Detach into a new session so the shell and its children can be
            # signalled as one group on timeout.
            start_new_session = True

        proc: subprocess.Popen | None = None
        try:
            popen_kwargs: dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                # No interactive input: detach stdin.
                "stdin": subprocess.DEVNULL,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "cwd": working_dir,
                "env": safe_env,
                **hidden_subprocess_kwargs(creationflags=creationflags),
            }
            if start_new_session:
                popen_kwargs["start_new_session"] = True
            if use_shell_flag:
                proc = subprocess.Popen(command, shell=True, **popen_kwargs)
            else:
                assert shell_args is not None
                proc = subprocess.Popen(shell_args, shell=False, **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                # Kill the whole tree (shell plus grandchildren such as a
                # server process), then drain pipes with a bounded grace
                # period. Waiting unbounded here is the hang: a surviving
                # grandchild holds the captured pipes open forever.
                _kill_process_tree(proc)
                stdout, stderr = _drain_after_kill(proc, exc.stdout, exc.stderr)
                return {
                    "stdout": _strip_ansi(_coerce_stream(stdout)),
                    "stderr": _strip_ansi(_coerce_stream(stderr)),
                    "exit_code": None,
                    "timed_out": True,
                }
            return {
                "stdout": _strip_ansi(_coerce_stream(stdout)),
                "stderr": _strip_ansi(_coerce_stream(stderr)),
                "exit_code": proc.returncode,
            }
        except FileNotFoundError as exc:
            return {"error": f"Working directory is not a directory: {working_dir}: {exc}"}
        except OSError as exc:
            return {"error": str(exc)}
        finally:
            if proc is not None:
                _close_pipes(proc)
                _reap(proc)
            if temp_script is not None:
                try:
                    temp_script.unlink(missing_ok=True)
                except Exception:
                    pass

    raw: dict[str, Any] = await asyncio.to_thread(_execute)

    if "error" in raw:
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, str(raw["error"]))

    timed_out = bool(raw.get("timed_out"))
    if timed_out:
        body = _timeout_message(timeout_ms)
        return f"{body}\n\n{_model_footer(None, True, warnings)}"

    combined = _combine_streams(str(raw.get("stdout", "")), str(raw.get("stderr", "")))
    body = combined.strip() or "(no output)"
    # Bounded in-memory preview with accurate capture-loss reporting.
    body, _truncated = _truncate_to_capture_limit(body)
    exit_code = raw.get("exit_code")
    try:
        exit_code = None if exit_code is None else int(exit_code)
    except (TypeError, ValueError):
        exit_code = None
    return f"{body}\n\n{_model_footer(exit_code, False, warnings)}"


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
