"""Manage the Linux systemd user service for k41-agent.

The service runs the server directly (without tray) so Ubuntu machines
restart the app on boot, including headless servers without DISPLAY.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SERVICE_NAME = "k41-agent.service"
SERVICE_DESCRIPTION = "K41 Agent Server"


@dataclass(frozen=True)
class ServicePaths:
    agent_home: Path
    app_dir: Path
    python_exe: Path
    service_file: Path


def get_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME


def get_agent_home() -> Path:
    for env_name in ("K41_AGENT_HOME", "AGENT_HOME"):
        value = os.environ.get(env_name)
        if value:
            return Path(value).expanduser().resolve()
    executable_path = Path(sys.executable).resolve()
    for parent in executable_path.parents:
        if parent.name.lower() == "envs":
            return parent.parent.resolve()
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home).expanduser().resolve() / "k41-agent"
    return Path.home() / ".local" / "share" / "k41-agent"


def resolve_service_paths(
    *,
    agent_home: Path | None = None,
    python_exe: Path | None = None,
) -> ServicePaths:
    home = Path(agent_home).expanduser().resolve() if agent_home else get_agent_home()
    app_dir = home / "app"
    if python_exe is not None:
        py = Path(python_exe).expanduser()
    else:
        candidate = home / "envs" / "bin" / "python"
        py = candidate if candidate.exists() else Path(sys.executable).resolve()
    return ServicePaths(
        agent_home=home,
        app_dir=app_dir,
        python_exe=py,
        service_file=get_service_path(),
    )


def render_unit(paths: ServicePaths) -> str:
    agent_home = str(paths.agent_home)
    app_dir = str(paths.app_dir)
    python_exe = str(paths.python_exe)
    runtime_home = str(Path.home() / ".k41-agent")
    envs_bin = str(paths.agent_home / "envs" / "bin")
    tools_dir = str(paths.agent_home / "tools")
    server_log = str(Path.home() / ".k41-agent" / "server.log")
    return f"""[Unit]
Description={SERVICE_DESCRIPTION}
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory={app_dir}
ExecStart={python_exe} -m agent.bootstrap.cli --foreground --no-tray
Restart=on-failure
RestartSec=5
Environment=K41_AGENT_HOME={agent_home}
Environment=AGENT_HOME={agent_home}
Environment=VIRTUAL_ENV={agent_home}/envs
Environment=PATH={envs_bin}:{tools_dir}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
StandardOutput=append:{server_log}
StandardError=append:{server_log}

[Install]
WantedBy=default.target
# Runtime data: {runtime_home}
"""


def is_systemd_available() -> tuple[bool, str]:
    if os.name == "nt" or sys.platform == "darwin":
        return False, "systemd user service is only supported on Linux."
    if shutil.which("systemctl") is None:
        return False, "systemctl was not found; systemd does not appear to be available."
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", "--property=Version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return False, f"Could not query systemd user manager: {exc}."
    if result.returncode != 0:
        hint = (result.stderr or result.stdout or "").strip()
        if "DBUS" in hint.upper() or "bus" in hint.lower():
            return False, (
                "systemd user manager is not reachable (no D-Bus user session). "
                "Log in normally or set XDG_RUNTIME_DIR; "
                f"detail: {hint}"
            )
        return False, f"systemctl --user is not usable: {hint or 'unknown error'}."
    return True, "ok"


def _ensure_systemd_available() -> None:
    available, reason = is_systemd_available()
    if not available:
        raise RuntimeError(reason)


def _run_systemctl(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "systemctl was not found; systemd does not appear to be available."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"systemctl --user timed out: {exc}.") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run systemctl --user: {exc}.") from exc


def is_service_installed(service_file: Path | None = None) -> bool:
    path = service_file or get_service_path()
    return path.exists()


def is_service_enabled() -> bool:
    try:
        result = _run_systemctl(["is-enabled", "--quiet", SERVICE_NAME])
        return result.returncode == 0
    except Exception:
        return False


def is_service_active() -> bool:
    try:
        result = _run_systemctl(["is-active", "--quiet", SERVICE_NAME])
        return result.returncode == 0
    except Exception:
        return False


def daemon_reload() -> None:
    _ensure_systemd_available()
    result = _run_systemctl(["daemon-reload"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"systemctl daemon-reload failed: {detail or 'unknown error'}")


def install_service(*, start: bool = True, paths: ServicePaths | None = None) -> ServicePaths:
    resolved = paths or resolve_service_paths()
    _ensure_systemd_available()
    resolved.service_file.parent.mkdir(parents=True, exist_ok=True)
    resolved.service_file.write_text(render_unit(resolved), encoding="utf-8")
    daemon_reload()
    args = ["enable", "--now", SERVICE_NAME] if start else ["enable", SERVICE_NAME]
    result = _run_systemctl(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not enable {SERVICE_NAME}: {detail or 'unknown error'}")
    return resolved


def uninstall_service(*, paths: ServicePaths | None = None) -> bool:
    resolved = paths or resolve_service_paths()
    available, _reason = is_systemd_available()
    if not available:
        # Still remove the unit file so reinstalls do not leave stale units.
        # Cleanup itself is success even without a running systemd manager.
        removed = False
        if resolved.service_file.exists():
            resolved.service_file.unlink(missing_ok=True)
            removed = True
        return removed
    _run_systemctl(["disable", "--now", SERVICE_NAME])
    removed = False
    if resolved.service_file.exists():
        resolved.service_file.unlink(missing_ok=True)
        removed = True
    try:
        daemon_reload()
    except RuntimeError:
        pass
    return removed


def enable_service(*, start: bool = True) -> None:
    _ensure_systemd_available()
    args = ["enable", "--now" if start else "", SERVICE_NAME]
    args = [a for a in args if a]
    result = _run_systemctl(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not enable {SERVICE_NAME}: {detail or 'unknown error'}")


def disable_service(*, stop: bool = True) -> None:
    _ensure_systemd_available()
    args = ["disable", "--now" if stop else "", SERVICE_NAME]
    args = [a for a in args if a]
    result = _run_systemctl(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not disable {SERVICE_NAME}: {detail or 'unknown error'}")


def start_service() -> None:
    _ensure_systemd_available()
    result = _run_systemctl(["start", SERVICE_NAME])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not start {SERVICE_NAME}: {detail or 'unknown error'}")


def stop_service() -> None:
    _ensure_systemd_available()
    result = _run_systemctl(["stop", SERVICE_NAME])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not stop {SERVICE_NAME}: {detail or 'unknown error'}")


def restart_service() -> None:
    _ensure_systemd_available()
    result = _run_systemctl(["restart", SERVICE_NAME])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not restart {SERVICE_NAME}: {detail or 'unknown error'}")


def ensure_linger() -> tuple[bool, str]:
    """Try to enable systemd linger so the user service starts at boot.

    Returns (ok, message). Never raises: callers should warn instead of
    failing the install when linger cannot be enabled without privileges.
    """
    if os.name == "nt" or sys.platform == "darwin":
        return False, "linger is only applicable on Linux with systemd."
    if shutil.which("loginctl") is None:
        return False, "loginctl was not found; linger could not be enabled."
    try:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        if not user:
            try:
                import getpass

                user = getpass.getuser()
            except Exception:
                user = ""
        if not user:
            return False, "Could not determine the current user for linger."
        result = subprocess.run(
            ["loginctl", "enable-linger", user],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, f"Linger enabled for user {user}."
        detail = (result.stderr or result.stdout or "").strip()
        return False, (
            f"Could not enable linger automatically ({detail or 'unknown error'}). "
            f"Run 'sudo loginctl enable-linger {user}' to start {SERVICE_NAME} at boot "
            "without login."
        )
    except Exception as exc:
        return False, f"Could not enable linger: {exc}."


def get_status() -> dict[str, object]:
    paths = resolve_service_paths()
    available, available_reason = is_systemd_available()
    return {
        "service": SERVICE_NAME,
        "service_file": str(paths.service_file),
        "agent_home": str(paths.agent_home),
        "app_dir": str(paths.app_dir),
        "python_exe": str(paths.python_exe),
        "systemd_available": available,
        "systemd_reason": available_reason,
        "installed": is_service_installed(paths.service_file),
        "enabled": is_service_enabled() if available else False,
        "active": is_service_active() if available else False,
    }


__all__ = [
    "SERVICE_NAME",
    "ServicePaths",
    "daemon_reload",
    "disable_service",
    "enable_service",
    "ensure_linger",
    "get_agent_home",
    "get_service_path",
    "get_status",
    "install_service",
    "is_service_active",
    "is_service_enabled",
    "is_service_installed",
    "is_systemd_available",
    "render_unit",
    "resolve_service_paths",
    "restart_service",
    "start_service",
    "stop_service",
    "uninstall_service",
]
