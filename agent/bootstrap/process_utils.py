"""Shared process helpers for k41 bootstrap (server + tray).

Centralizes logic previously duplicated across cli.py, tray.py and update.py:
- process liveness check
- cmdline retrieval (psutil -> wmic/ps fallback)
- k41 / tray process identification
- tray pid file helpers with consistent empty-cmdline handling

Empty cmdline semantics:
- psutil not installed OR wmic/ps returned "" OR AccessDenied -> we cannot
  verify the command line. To avoid spawning duplicate instances we treat
  the pid as valid when it is alive (lenient), unless the caller explicitly
  requires strict verification.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TRAY_EXECUTABLE_NAME = "k41-agent-tray.exe"

# Markers that uniquely identify a tray process cmdline. Deliberately NOT the
# bare word "tray": server cmdlines contain "--no-tray" and would otherwise
# false-positive when a dead tray's PID gets reused by a server process.
TRAY_CMDLINE_MARKERS = ("agent.bootstrap.tray", TRAY_EXECUTABLE_NAME)


def _is_tray_cmdline(cmd: str) -> bool:
    """Return True if a (lowercased) cmdline belongs to the tray process."""
    return any(marker in cmd for marker in TRAY_CMDLINE_MARKERS)


def get_dedicated_tray_executable(
    python_executable: str | None = None,
) -> str | None:
    """Return the dedicated tray GUI executable if available (Windows only).

    The executable is generated from [project.gui-scripts] in pyproject.toml
    (k41-agent-tray.exe). Preferring it over pythonw.exe makes Windows show
    "k41-agent-tray.exe" instead of "pythonw.exe" under Settings > Apps >
    Startup. Returns None when unavailable; callers must fall back to
    "pythonw.exe -m agent.bootstrap.tray".
    """
    if os.name != "nt":
        return None
    base = Path(python_executable) if python_executable else Path(sys.executable)
    candidate = base.with_name(TRAY_EXECUTABLE_NAME)
    return str(candidate) if candidate.exists() else None


def is_process_alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def get_process_cmdline(pid: int) -> str:
    try:
        import psutil

        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline()
            if cmdline:
                return " ".join(cmdline).lower()
            return proc.name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return ""
    except ImportError:
        pass
    try:
        from agent.shared.infrastructure.subprocess_utils import (
            hidden_subprocess_kwargs,
        )

        if os.name == "nt":
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                capture_output=True,
                text=True,
                **hidden_subprocess_kwargs(),
            )
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True,
                text=True,
            )
        return result.stdout.lower()
    except Exception:
        return ""


def is_k41_process(pid: int, *, strict: bool = False) -> bool:
    try:
        cmd = get_process_cmdline(pid)
        if not cmd:
            if strict:
                # Strict mode: cannot verify -> treat as NOT matching
                return False
            # Lenient: be lenient if pid is alive to avoid duplicate spawn.
            return is_process_alive(pid)
        # Exclude tray cmdlines: "k41" also appears in "k41-agent-tray.exe"
        # and in the install path, so a reused tray PID would otherwise be
        # mistaken for a server process.
        return ("k41" in cmd or "agent.bootstrap.cli" in cmd) and not _is_tray_cmdline(cmd)
    except Exception:
        return False


def is_tray_process(pid: int, *, strict: bool = False) -> bool:
    try:
        cmd = get_process_cmdline(pid)
        if not cmd:
            if strict:
                # Strict mode for termination: unverifiable pid must not be
                # considered a tray process to avoid killing an unrelated
                # process that reused the PID.
                return False
            # Lenient mode for spawn-guard: treat alive pid as valid tray
            # when we cannot verify to avoid duplicate instances.
            return is_process_alive(pid)
        return _is_tray_cmdline(cmd)
    except Exception:
        return False


def is_tray_process_verified(pid: int) -> bool:
    """Strict verification that pid is a tray process (empty cmdline -> False)."""
    return is_tray_process(pid, strict=True)


def is_k41_process_verified(pid: int) -> bool:
    """Strict verification that pid is a k41 process (empty cmdline -> False)."""
    return is_k41_process(pid, strict=True)


def _read_pid_file(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def is_tray_running(
    pid_file: Path,
    *,
    lenient_on_unverifiable: bool = True,
) -> bool:
    """Check if tray pid file points to a running tray.

    When cmdline cannot be retrieved (empty string) and
    lenient_on_unverifiable is True, an alive pid is considered running
    to avoid duplicate instances. This matches the intended
    "Be lenient: if pid exists but we cannot verify ... still consider
    running" contract.
    """
    if not pid_file.exists():
        return False
    pid = _read_pid_file(pid_file)
    if pid is None:
        return False
    if not is_process_alive(pid):
        return False
    cmd = get_process_cmdline(pid)
    if not cmd:
        return lenient_on_unverifiable
    if is_tray_process(pid, strict=True):
        return True
    return False


def spawn_detached_process(
    cmd: list[str],
    log_file: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    """Spawn a detached background process with platform-specific flags.

    Centralizes the duplicated `subprocess.Popen` logic that previously
    lived in cli._daemonize, cli._spawn_tray_process, update.start_server,
    update.start_tray and tray._start_server.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    spawn_env = (os.environ if env is None else env).copy()
    if "VIRTUAL_ENV" not in spawn_env and sys.prefix != sys.base_prefix:
        spawn_env["VIRTUAL_ENV"] = sys.prefix

    with log_file.open("ab") as lf:
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
            startupinfo.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
            subprocess.Popen(
                cmd,
                env=spawn_env,
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                stdout=lf,
                stderr=subprocess.STDOUT,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                    | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
                    | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                ),
                startupinfo=startupinfo,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                cmd,
                env=spawn_env,
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )


def is_server_running(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    pid = _read_pid_file(pid_file)
    if pid is None:
        return False
    if not is_process_alive(pid):
        return False
    cmd = get_process_cmdline(pid)
    if not cmd:
        # Lenient: alive pid with unverifiable cmdline is considered running
        return True
    return is_k41_process(pid)


def get_running_pid(pid_file: Path, *, check_fn) -> int | None:
    """Generic helper: return pid if file exists, alive and passes check_fn."""
    if not pid_file.exists():
        return None
    pid = _read_pid_file(pid_file)
    if pid is None:
        return None
    if not is_process_alive(pid):
        return None
    if not check_fn(pid):
        # For tray: if cmdline empty we already handled in is_tray_running;
        # here caller expects strict check, so empty cmdline -> no pid.
        return None
    return pid


def get_running_tray_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    pid = _read_pid_file(pid_file)
    if pid is None:
        return None
    if not is_process_alive(pid):
        return None
    cmd = get_process_cmdline(pid)
    if not cmd:
        # Lenient: treat unverifiable but alive pid as running tray
        return pid
    if _is_tray_cmdline(cmd):
        return pid
    return None


def get_running_server_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    pid = _read_pid_file(pid_file)
    if pid is None:
        return None
    if not is_process_alive(pid):
        return None
    cmd = get_process_cmdline(pid)
    if not cmd:
        return pid
    # Exclude tray cmdlines so a server PID reused by the tray is not
    # reported as a running server.
    if ("k41" in cmd or "agent.bootstrap.cli" in cmd) and not _is_tray_cmdline(cmd):
        return pid
    return None
