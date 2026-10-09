from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

import httpx
import psutil

from agent.bootstrap.version import APP_VERSION, PACKAGE_NAME
from agent.bootstrap.update_state import (
    UpdateBusyError,
    get_process_start_time,
    read_update_status,
    update_lock,
    write_update_status,
)

DEFAULT_OWNER = "k4han"
DEFAULT_REPO = "k41-agent"
DEFAULT_ARTIFACT_NAME = "k41-agent-release.zip"
BACKUP_PREFIX = "app-"
DOWNLOAD_TIMEOUT_SECONDS = 60.0
SERVER_STOP_TIMEOUT_SECONDS = 15.0
SERVER_TERMINATE_TIMEOUT_SECONDS = 10.0
SERVER_KILL_TIMEOUT_SECONDS = 5.0
SERVER_LOG_FILE = Path.home() / ".k41-agent" / "server.log"
PID_FILE = Path.home() / ".k41-agent" / "server.pid"
TRAY_PID_FILE = Path.home() / ".k41-agent" / "tray.pid"
SHUTDOWN_SIGNAL = Path.home() / ".k41-agent" / "shutdown.signal"
logger = logging.getLogger(__name__)


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagedInstall:
    agent_home: Path
    app_dir: Path
    backup_dir: Path
    download_dir: Path
    tools_dir: Path
    envs_dir: Path
    uv_exe: Path
    python_exe: Path


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: str
    asset_url: str
    html_url: str
    name: str = ""
    body: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class UpdateOptions:
    check_only: bool = False
    force: bool = False
    yes: bool = False
    owner: str = DEFAULT_OWNER
    repo: str = DEFAULT_REPO
    artifact_name: str = DEFAULT_ARTIFACT_NAME
    current_version: str | None = None
    module_file: Path | None = None
    executable: str | None = None
    update_id: str | None = None


@dataclass(frozen=True)
class UpdateResult:
    status: str
    current_version: str
    latest_version: str
    backup_path: Path | None = None
    restarted: bool = False


Echo = Callable[[str], None]
Confirm = Callable[[str], bool]


def run_update(
    options: UpdateOptions,
    *,
    echo: Echo | None = None,
    confirm: Confirm | None = None,
) -> UpdateResult:
    if options.check_only:
        return _run_update(options, echo=echo, confirm=confirm)
    try:
        install = resolve_managed_install(
            module_file=options.module_file, executable=options.executable,
        )
    except UpdateError as exc:
        if options.update_id is not None:
            try:
                agent_home = detect_agent_home(executable=options.executable)
                with update_lock(agent_home):
                    previous = read_update_status(agent_home)
                    if previous and previous.get("update_id") == options.update_id and previous.get("status") == "queued":
                        write_update_status(agent_home, {**previous, "status": "failed", "error": str(exc)})
            except (UpdateError, UpdateBusyError, OSError) as status_exc:
                if echo is not None:
                    echo(f"Warning: Could not record update failure: {status_exc}")
        raise
    try:
        with update_lock(install.agent_home):
            previous = read_update_status(install.agent_home)
            if options.update_id is not None:
                if previous is None or previous.get("update_id") != options.update_id or previous.get("status") != "queued":
                    raise UpdateError("The queued update is no longer available. Please try again.")
            elif previous and previous.get("status") in {"queued", "running"}:
                raise UpdateBusyError("Another update is already in progress.")
            status = {
                "update_id": options.update_id or uuid4().hex,
                "status": "running",
                "pid": os.getpid(),
                "process_started_at": get_process_start_time(os.getpid()),
                "error": None,
            }
            previous_cwd = Path.cwd()
            try:
                status["current_version"] = options.current_version or read_project_version(install.app_dir)
                write_update_status(install.agent_home, status)
                # Windows keeps a handle to the working directory; leave app before deleting it.
                os.chdir(install.agent_home)
                result = _run_update(options, echo=echo, confirm=confirm, install=install)
                write_update_status(install.agent_home, {
                    **status, "status": result.status, "latest_version": result.latest_version,
                    "restarted": result.restarted,
                })
                return result
            except Exception as exc:
                write_update_status(install.agent_home, {**status, "status": "failed", "error": str(exc)})
                if isinstance(exc, UpdateError):
                    raise
                raise UpdateError(f"Update failed: {exc}") from exc
            finally:
                os.chdir(previous_cwd if previous_cwd.is_dir() else install.agent_home)
    except UpdateBusyError as exc:
        raise UpdateError(str(exc)) from exc


def _run_update(
    options: UpdateOptions,
    *,
    echo: Echo | None = None,
    confirm: Confirm | None = None,
    install: ManagedInstall | None = None,
) -> UpdateResult:
    echo = echo or (lambda message: None)
    confirm = confirm or (lambda message: True)

    current_version = options.current_version or APP_VERSION
    if not options.check_only:
        install = install or resolve_managed_install(
            module_file=options.module_file,
            executable=options.executable,
        )
        current_version = options.current_version or read_project_version(install.app_dir)

    release = fetch_latest_release(
        owner=options.owner,
        repo=options.repo,
        artifact_name=options.artifact_name,
    )

    has_update = is_version_newer(release.version, current_version)
    echo(f"Current version: {current_version}")
    echo(f"Latest version: {release.version}")

    if options.check_only:
        if has_update:
            echo(f"Update available: {current_version} -> {release.version}")
            return UpdateResult("available", current_version, release.version)
        echo("K41 Agent is already up to date.")
        return UpdateResult("current", current_version, release.version)

    if not has_update and not options.force:
        echo("K41 Agent is already up to date.")
        return UpdateResult("current", current_version, release.version)

    if not options.yes and not confirm(
        f"Update K41 Agent from {current_version} to {release.version}?"
    ):
        echo("Update cancelled.")
        return UpdateResult("cancelled", current_version, release.version)

    assert install is not None
    install.download_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = install.download_dir / options.artifact_name
    extract_dir = install.download_dir / "update-source"

    echo(f"Downloading {release.asset_url}")
    download_release_artifact(release.asset_url, artifact_path)
    echo("Verifying release artifact")
    source_root = extract_release_artifact(artifact_path, extract_dir)
    artifact_version = read_project_version(source_root)
    if normalize_version(artifact_version) != normalize_version(release.version):
        raise UpdateError(
            f"Release artifact version {artifact_version} does not match release {release.version}."
        )

    running_pid = get_running_server_pid()
    running_tray_pid = get_running_tray_pid()
    systemd_active = is_systemd_service_active()
    should_restart_systemd = systemd_active
    should_restart = running_pid is not None and not systemd_active
    should_restart_tray = running_tray_pid is not None
    systemd_stopped = False
    backup_path: Path | None = None
    try:
        if systemd_active:
            echo("Stopping systemd service k41-agent.service")
            if not stop_systemd_service():
                raise UpdateError("Could not stop systemd service; the installation was not changed.")
            systemd_stopped = True
            SHUTDOWN_SIGNAL.unlink(missing_ok=True)
            running_pid = get_running_server_pid()
        if running_tray_pid is not None:
            echo(f"Stopping running tray (PID {running_tray_pid})")
            if not stop_running_tray(running_tray_pid) and is_process_alive(running_tray_pid):
                raise UpdateError("Could not stop the tray; the installation was not changed.")
        if running_pid is not None:
            echo(f"Stopping running server (PID {running_pid})")
            stop_running_server(running_pid)
        backup_path = backup_app_source(install, current_version)
        echo(f"Backup created at {backup_path}")

        replace_app_source(install, source_root)
        sync_app(install)
        initialize_app(install)
    except Exception as exc:
        rollback_error: Exception | None = None
        if backup_path is not None:
            echo("Update failed. Restoring previous source from backup.")
            try:
                restore_app_source(install, backup_path)
                sync_app(install)
            except Exception as rollback_exc:
                rollback_error = rollback_exc
            try:
                prune_backups(install.backup_dir, keep=backup_path)
            except (OSError, UpdateError) as cleanup_exc:
                echo(f"Warning: Could not prune update backups: {cleanup_exc}")
        if systemd_stopped:
            try:
                if not refresh_systemd_unit(install):
                    echo("Warning: Could not refresh systemd unit file")
            except Exception as refresh_exc:
                echo(f"Warning: Could not refresh systemd unit file: {refresh_exc}")
            if not restart_systemd_service():
                echo("Warning: Could not restart systemd service; starting server directly")
                start_server(install)
        elif should_restart and get_running_server_pid() is None:
            start_server(install)
        if should_restart_tray:
            if get_running_tray_pid() is None:
                try:
                    start_tray(install)
                except Exception as tray_exc:
                    echo(f"Warning: Could not restart tray: {tray_exc}")
        if rollback_error is not None:
            raise UpdateError(f"Update failed: {exc}. Rollback failed: {rollback_error}") from exc
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Update failed: {exc}") from exc

    if should_restart_systemd:
        echo("Restarting systemd service k41-agent.service")
        try:
            if not refresh_systemd_unit(install):
                echo("Warning: Could not refresh systemd unit file")
        except Exception as exc:
            echo(f"Warning: Could not refresh systemd unit file: {exc}")
        if not restart_systemd_service():
            echo("Warning: Could not restart systemd service; starting server directly")
            start_server(install)
            should_restart = True
        else:
            should_restart = True
        # Brief pause to let server write PID file before checking tray
        time.sleep(0.5)
    elif should_restart:
        echo("Restarting server")
        start_server(install)
        # Brief pause to let server write PID file before checking tray
        time.sleep(0.5)
    if should_restart_tray:
        # Avoid spawning tray twice: server is started with --no-tray so it
        # will not auto-spawn tray; still check pid before explicit start.
        # Poll briefly to handle race where old tray pid file lingers.
        tray_running_now = get_running_tray_pid()
        if tray_running_now is not None:
            echo("Tray already running after server restart, skipping explicit tray start.")
        else:
            # Ensure any stale pid file from previous tray is cleaned
            # before spawning - wait a moment for OS to release handle
            time.sleep(0.3)
            if get_running_tray_pid() is not None:
                echo("Tray already running after server restart, skipping explicit tray start.")
            else:
                echo("Restarting tray")
                try:
                    start_tray(install)
                except Exception as exc:
                    echo(f"Warning: Could not restart tray: {exc}")

    try:
        prune_backups(install.backup_dir, keep=backup_path)
        cleanup_downloads(install)
    except (OSError, UpdateError) as exc:
        echo(f"Warning: Could not clean up update files: {exc}")
    echo(f"Updated K41 Agent to {release.version}.")
    return UpdateResult(
        "updated",
        current_version,
        release.version,
        backup_path=backup_path,
        restarted=should_restart,
    )


def resolve_managed_install(
    *,
    module_file: Path | None = None,
    executable: str | None = None,
) -> ManagedInstall:
    agent_home = detect_agent_home(executable=executable)
    app_dir = agent_home / "app"
    install = ManagedInstall(
        agent_home=agent_home,
        app_dir=app_dir,
        backup_dir=agent_home / "backup",
        download_dir=agent_home / "download",
        tools_dir=agent_home / "tools",
        envs_dir=agent_home / "envs",
        uv_exe=agent_home / "tools" / executable_name("uv"),
        python_exe=python_executable(agent_home),
    )
    validate_managed_install(install, module_file=module_file)
    return install


def _agent_home_from_envs_parent(path: Path) -> Path | None:
    if path.name.lower() == "envs":
        return path.parent
    for parent in path.parents:
        if parent.name.lower() == "envs":
            return parent.parent
    return None


def detect_agent_home(*, executable: str | None = None) -> Path:
    for env_name in ("K41_AGENT_HOME", "AGENT_HOME"):
        value = os.environ.get(env_name)
        if value:
            return Path(value).expanduser().resolve()

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidate = _agent_home_from_envs_parent(Path(virtual_env).expanduser())
        if candidate is not None:
            return candidate.resolve()

    raw_executable = executable or sys.executable
    for candidate_path in (Path(raw_executable), Path(raw_executable).resolve()):
        candidate = _agent_home_from_envs_parent(candidate_path)
        if candidate is not None:
            return candidate.resolve()

    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if parent.name == "app":
            return parent.parent.resolve()

    raise UpdateError(
        "Could not determine AGENT_HOME. Run updates from an installed K41 Agent "
        f"(tried executable={raw_executable}, module={module_path}). "
        "Set K41_AGENT_HOME to your install directory, e.g. "
        "export K41_AGENT_HOME=~/.local/share/k41-agent."
    )


def validate_managed_install(
    install: ManagedInstall,
    *,
    module_file: Path | None = None,
) -> None:
    if not install.app_dir.exists():
        raise UpdateError(f"Managed app directory was not found: {install.app_dir}")
    if not install.uv_exe.exists():
        raise UpdateError(f"uv executable was not found: {install.uv_exe}")
    if not install.python_exe.exists():
        raise UpdateError(f"Python executable was not found: {install.python_exe}")

    current_file = Path(module_file or __file__).resolve()
    if not is_relative_to(current_file, install.app_dir.resolve()):
        raise UpdateError(
            "Refusing to update because the CLI is not running from "
            f"{install.app_dir}."
        )

    name, _version = read_project_metadata(install.app_dir)
    if name != PACKAGE_NAME:
        raise UpdateError(f"Managed app is not {PACKAGE_NAME}: {install.app_dir}")


def executable_name(stem: str) -> str:
    return f"{stem}.exe" if os.name == "nt" else stem


def python_executable(agent_home: Path) -> Path:
    if os.name == "nt":
        candidate = agent_home / "envs" / "Scripts" / "python.exe"
    else:
        candidate = agent_home / "envs" / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(sys.executable).resolve()


def fetch_latest_release(
    *,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    artifact_name: str = DEFAULT_ARTIFACT_NAME,
) -> ReleaseInfo:
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "k41-agent-updater",
    }
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(api_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise UpdateError(f"Could not check latest release: {exc}") from exc
    except ValueError as exc:
        raise UpdateError("GitHub release response was not valid JSON.") from exc

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateError("Latest GitHub release did not include a tag name.")

    asset_url = ""
    for asset in payload.get("assets") or []:
        if str(asset.get("name") or "") == artifact_name:
            asset_url = str(asset.get("browser_download_url") or "")
            break
    if not asset_url:
        raise UpdateError(
            f"Latest GitHub release does not contain {artifact_name}."
        )

    return ReleaseInfo(
        tag_name=tag_name,
        version=normalize_version(tag_name),
        asset_url=asset_url,
        html_url=str(payload.get("html_url") or ""),
        name=str(payload.get("name") or tag_name),
        body=str(payload.get("body") or ""),
        published_at=str(payload.get("published_at") or ""),
    )


def is_managed_install(
    *,
    module_file: Path | None = None,
    executable: str | None = None,
) -> bool:
    try:
        resolve_managed_install(module_file=module_file, executable=executable)
        return True
    except UpdateError:
        return False


def download_release_artifact(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_destination = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "k41-agent-updater"},
        ) as response:
            response.raise_for_status()
            with temporary_destination.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        temporary_destination.replace(destination)
    except httpx.HTTPError as exc:
        temporary_destination.unlink(missing_ok=True)
        raise UpdateError(f"Could not download release artifact: {exc}") from exc
    except OSError as exc:
        temporary_destination.unlink(missing_ok=True)
        raise UpdateError(f"Could not write release artifact: {exc}") from exc


def extract_release_artifact(zip_path: Path, extract_dir: Path) -> Path:
    if extract_dir.exists():
        safe_remove_tree(extract_dir, extract_dir.parent)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            safe_extract_zip(archive, extract_dir)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError(f"Release artifact is not a valid zip file: {exc}") from exc

    root = find_k41_project_root(extract_dir)
    if root is None:
        raise UpdateError("Release artifact did not contain a k41-agent project root.")
    assert_dashboard_build(root)
    return root


def safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    destination_resolved = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if not is_relative_to(target, destination_resolved):
            raise UpdateError(f"Refusing to extract unsafe zip member: {member.filename}")
    archive.extractall(destination)


def find_k41_project_root(path: Path) -> Path | None:
    if is_k41_project_root(path):
        return path
    for project_file in path.rglob("pyproject.toml"):
        candidate = project_file.parent
        if is_k41_project_root(candidate):
            return candidate
    return None


def is_k41_project_root(path: Path) -> bool:
    try:
        name, _version = read_project_metadata(path)
    except UpdateError:
        return False
    return name == PACKAGE_NAME


def read_project_metadata(root: Path) -> tuple[str, str]:
    project_file = root / "pyproject.toml"
    try:
        data = tomllib.loads(project_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UpdateError(f"Could not read {project_file}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise UpdateError(f"Could not parse {project_file}: {exc}") from exc
    project = data.get("project", {})
    name = str(project.get("name") or "")
    version = str(project.get("version") or "")
    if not name or not version:
        raise UpdateError(f"{project_file} is missing project name or version.")
    return name, version


def read_project_version(root: Path) -> str:
    _name, version = read_project_metadata(root)
    return version


def assert_dashboard_build(root: Path) -> None:
    index_file = root / "agent" / "delivery" / "http" / "dashboard" / "static" / "index.html"
    if not index_file.exists():
        raise UpdateError(
            "Dashboard frontend build is missing from the release artifact. "
            f"Expected {index_file}."
        )


def backup_app_source(install: ManagedInstall, current_version: str) -> Path:
    install.backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    backup_path = install.backup_dir / f"{BACKUP_PREFIX}{current_version}-{timestamp}"
    copy_tree(install.app_dir, backup_path)
    return backup_path


def replace_app_source(install: ManagedInstall, source_root: Path) -> None:
    safe_remove_tree(install.app_dir, install.agent_home)
    copy_tree(source_root, install.app_dir)


def restore_app_source(install: ManagedInstall, backup_path: Path) -> None:
    if install.app_dir.exists():
        safe_remove_tree(install.app_dir, install.agent_home)
    copy_tree(backup_path, install.app_dir)


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        safe_remove_tree(destination, destination.parent)
    # Keep in sync with install.ps1, install.sh and release.yml.
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".github",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".tmp_*",
            "build",
            "dist",
            "node_modules",
            "wheels",
            "local-dev",
            "data",
            "*.egg-info",
            "*.pyc",
            "*.pyo",
            ".env",
            ".env.*",
        ),
    )


def prune_backups(backup_dir: Path, *, keep: Path | None) -> None:
    if not backup_dir.exists():
        return
    keep_resolved = keep.resolve() if keep is not None and keep.exists() else None
    backups = sorted(
        (
            path
            for path in backup_dir.iterdir()
            if path.is_dir() and path.name.startswith(BACKUP_PREFIX)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for backup in backups:
        if keep_resolved is not None and backup.resolve() == keep_resolved:
            continue
        safe_remove_tree(backup, backup_dir)


def sync_app(install: ManagedInstall) -> None:
    scripts_dir = install.python_exe.parent
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(install.envs_dir)
    env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"
    run_command(
        [
            str(install.uv_exe),
            "sync",
            "--active",
            "--frozen",
            "--no-dev",
            "--compile-bytecode",
        ],
        cwd=install.app_dir,
        env=env,
    )


def initialize_app(install: ManagedInstall) -> None:
    run_command(
        [str(install.python_exe), "-m", "agent.bootstrap.cli", "init"],
        cwd=install.app_dir,
        env=os.environ.copy(),
    )


def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        raise UpdateError(
            f"{command[0]} failed with exit code {exc.returncode}."
        ) from exc
    except OSError as exc:
        raise UpdateError(f"Could not run {command[0]}: {exc}") from exc


def get_running_server_pid() -> int | None:
    from agent.bootstrap.process_utils import (
        get_running_server_pid as _shared_get_server_pid,
    )

    return _shared_get_server_pid(PID_FILE)


def _get_process_cmdline(pid: int) -> str:
    from agent.bootstrap.process_utils import get_process_cmdline as _shared_get_cmd

    return _shared_get_cmd(pid)


def get_running_tray_pid() -> int | None:
    from agent.bootstrap.process_utils import (
        get_running_tray_pid as _shared_get_tray_pid,
    )

    return _shared_get_tray_pid(TRAY_PID_FILE)


def stop_running_tray(pid: int) -> bool:
    """Stop tray pid if verified. Returns True if stopped/cleaned, False otherwise."""
    cmd = _get_process_cmdline(pid)
    if not cmd:
        # Unverifiable cmdline -> refuse to terminate to avoid killing unrelated process
        return False
    if "tray" not in cmd and "agent.bootstrap.tray" not in cmd:
        # Verified non-tray -> stale pid file
        TRAY_PID_FILE.unlink(missing_ok=True)
        return False
    try:
        import psutil

        try:
            proc = psutil.Process(pid)
            proc.terminate()
            deadline = time.time() + SERVER_STOP_TIMEOUT_SECONDS
            while time.time() < deadline:
                if not is_process_alive(pid):
                    TRAY_PID_FILE.unlink(missing_ok=True)
                    return True
                time.sleep(0.5)
            try:
                proc.kill()
            except Exception:
                pass
        except psutil.NoSuchProcess:
            TRAY_PID_FILE.unlink(missing_ok=True)
            return True
    except ImportError:
        try:
            if os.name == "nt":
                from agent.shared.infrastructure.subprocess_utils import (
                    hidden_subprocess_kwargs,
                )

                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    **hidden_subprocess_kwargs(),
                )
            else:
                os.kill(pid, 15)
                time.sleep(1)
                if is_process_alive(pid):
                    os.kill(pid, 9)
        except Exception:
            pass
    deadline = time.time() + SERVER_STOP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if not is_process_alive(pid):
            TRAY_PID_FILE.unlink(missing_ok=True)
            return True
        time.sleep(0.5)
    # Keep pid file if process still alive to avoid duplicate spawn.
    if not is_process_alive(pid):
        TRAY_PID_FILE.unlink(missing_ok=True)
        return True
    return False


def stop_running_server(pid: int) -> None:
    # Keep the same Process object: psutil checks creation time before sending
    # signals, protecting unrelated processes if the PID is reused while waiting.
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        _cleanup_server_stop_files(pid)
        return
    except psutil.AccessDenied:
        process = None

    children: list[psutil.Process] = []

    def tracked_process_alive(target: psutil.Process) -> bool:
        try:
            return target.is_running() and target.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
        except psutil.NoSuchProcess:
            return False

    def wait_for_exit(timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            server_alive = is_process_alive(pid) and (process is None or process.is_running())
            if not server_alive and not any(tracked_process_alive(child) for child in children):
                _cleanup_server_stop_files(pid)
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    SHUTDOWN_SIGNAL.parent.mkdir(parents=True, exist_ok=True)
    SHUTDOWN_SIGNAL.write_text(str(pid), encoding="utf-8")
    if wait_for_exit(SERVER_STOP_TIMEOUT_SECONDS):
        return

    try:
        if process is None or not _is_server_command(process.cmdline()):
            raise UpdateError(
                f"Server process {pid} did not stop and its identity could not be verified. "
                "The installation was not changed."
            )
        # Capture descendants before stopping the parent: orphaned children
        # cannot reliably be discovered afterward and may keep app files open.
        # Dashboard updates run as a child of the server. Preserve the updater
        # and any launcher ancestors so tree shutdown cannot stop this update.
        protected_pids = {os.getpid()}
        protected_pids.update(
            ancestor.pid for ancestor in psutil.Process(os.getpid()).parents() if ancestor.pid != pid
        )
        children = [child for child in process.children(recursive=True) if child.pid not in protected_pids]
        targets = [*reversed(children), process]
        logger.warning("Server process %s did not stop gracefully; requesting termination.", pid)
        for target in targets:
            try:
                if tracked_process_alive(target):
                    target.terminate()
            except psutil.NoSuchProcess:
                pass
        if wait_for_exit(SERVER_TERMINATE_TIMEOUT_SECONDS):
            return
        logger.warning("Server process %s did not terminate; forcing it to stop.", pid)
        for target in targets:
            try:
                if tracked_process_alive(target):
                    target.kill()
            except psutil.NoSuchProcess:
                pass
        if wait_for_exit(SERVER_KILL_TIMEOUT_SECONDS):
            return
    except psutil.NoSuchProcess:
        _cleanup_server_stop_files(pid)
        return
    except psutil.AccessDenied as exc:
        raise UpdateError(f"Permission denied while stopping server process {pid}.") from exc
    raise UpdateError(f"Server process {pid} did not stop within timeout.")


def _is_server_command(command: list[str]) -> bool:
    if len(command) >= 3 and command[1:3] == ["-m", "agent.bootstrap.cli"]:
        arguments = command[3:]
    elif command and Path(command[0]).name.lower() in {"k41", "k41.exe"}:
        arguments = command[1:]
    elif len(command) >= 2 and Path(command[1]).name.lower() in {"k41", "k41.exe"}:
        arguments = command[2:]
    else:
        return False
    # Subcommands such as update, init and tray must never be terminated here.
    server_flags = {"--foreground", "-f", "--no-tray", "--tray", "--verbose", "--quiet", "-q"}
    return all(argument in server_flags for argument in arguments)


def _cleanup_server_stop_files(pid: int) -> None:
    for path in (PID_FILE, SHUTDOWN_SIGNAL):
        try:
            if path.read_text(encoding="utf-8").strip() == str(pid):
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


def start_tray(install: ManagedInstall) -> None:
    from agent.bootstrap.process_utils import (
        get_dedicated_tray_executable,
        spawn_detached_process,
    )

    env = os.environ.copy()
    env["K41_TRAY_DAEMONIZED"] = "1"
    tray_log = Path.home() / ".k41-agent" / "tray.log"
    tray_exe = install.python_exe
    tray_argv_tail = ["-m", "agent.bootstrap.tray"]
    if os.name == "nt":
        # Prefer the dedicated, friendly-named tray GUI executable so Windows
        # shows "k41-agent-tray.exe" instead of "pythonw.exe" in Startup apps.
        dedicated = get_dedicated_tray_executable(str(tray_exe))
        if dedicated is not None:
            tray_exe = dedicated
            tray_argv_tail = []
        else:
            # Prefer pythonw for tray on Windows to avoid console window
            pythonw = Path(str(tray_exe)).with_name("pythonw.exe")
            if pythonw.exists():
                tray_exe = pythonw
    spawn_detached_process(
        [str(tray_exe), *tray_argv_tail],
        tray_log,
        env=env,
        cwd=install.app_dir,
    )


def start_server(install: ManagedInstall) -> None:
    from agent.bootstrap.process_utils import spawn_detached_process

    env = os.environ.copy()
    env["K41_DAEMONIZED"] = "1"
    spawn_detached_process(
        [str(install.python_exe), "-m", "agent.bootstrap.cli", "--no-tray"],
        SERVER_LOG_FILE,
        env=env,
        cwd=install.app_dir,
    )


def is_systemd_service_active() -> bool:
    try:
        from agent.bootstrap.service import is_service_active, is_systemd_available

        available, _ = is_systemd_available()
        if not available:
            return False
        return is_service_active()
    except Exception:
        return False


def stop_systemd_service() -> bool:
    try:
        from agent.bootstrap.service import is_systemd_available, stop_service

        available, _ = is_systemd_available()
        if not available:
            return False
        stop_service()
        return True
    except Exception:
        return False


def refresh_systemd_unit(install: ManagedInstall) -> bool:
    try:
        from agent.bootstrap.service import (
            ServicePaths,
            daemon_reload,
            get_service_path,
            is_service_installed,
            is_systemd_available,
            render_unit,
        )

        available, _ = is_systemd_available()
        if not available:
            return False
        service_file = get_service_path()
        if not is_service_installed(service_file):
            return False
        paths = ServicePaths(
            agent_home=install.agent_home,
            app_dir=install.app_dir,
            python_exe=install.python_exe,
            service_file=service_file,
        )
        service_file.parent.mkdir(parents=True, exist_ok=True)
        service_file.write_text(render_unit(paths), encoding="utf-8")
        try:
            daemon_reload()
        except Exception:
            pass
        return True
    except Exception:
        return False


def restart_systemd_service(*, reload_daemon: bool = True) -> bool:
    try:
        from agent.bootstrap.service import daemon_reload, restart_service

        if reload_daemon:
            try:
                daemon_reload()
            except Exception:
                pass
        restart_service()
        return True
    except Exception:
        return False


def is_process_alive(pid: int) -> bool:
    from agent.bootstrap.process_utils import is_process_alive as _shared_alive

    return _shared_alive(pid)


def is_k41_process(pid: int) -> bool:
    from agent.bootstrap.process_utils import is_k41_process as _shared_k41

    return _shared_k41(pid)


def cleanup_downloads(install: ManagedInstall) -> None:
    if not install.download_dir.exists():
        return
    for child in install.download_dir.iterdir():
        if child.is_dir():
            safe_remove_tree(child, install.download_dir)
        else:
            child.unlink(missing_ok=True)


def safe_remove_tree(path: Path, allowed_parent: Path) -> None:
    resolved_path = path.resolve()
    resolved_parent = allowed_parent.resolve()
    if resolved_path == resolved_parent or not is_relative_to(resolved_path, resolved_parent):
        raise UpdateError(f"Refusing to remove unsafe path: {path}")
    shutil.rmtree(resolved_path)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize_version(value: str) -> str:
    normalized = value.strip()
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    return normalized


def is_version_newer(latest: str, current: str) -> bool:
    latest_key = version_key(latest)
    current_key = version_key(current)
    if latest_key is None or current_key is None:
        return normalize_version(latest) != normalize_version(current)
    return latest_key > current_key


def version_key(value: str) -> tuple[int, int, int] | None:
    match = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", value.strip())
    if not match:
        return None
    parts = [int(part) if part is not None else 0 for part in match.groups()]
    return parts[0], parts[1], parts[2]


__all__ = [
    "DEFAULT_ARTIFACT_NAME",
    "DEFAULT_OWNER",
    "DEFAULT_REPO",
    "ManagedInstall",
    "ReleaseInfo",
    "UpdateError",
    "UpdateOptions",
    "UpdateResult",
    "detect_agent_home",
    "extract_release_artifact",
    "fetch_latest_release",
    "is_managed_install",
    "is_version_newer",
    "read_project_metadata",
    "resolve_managed_install",
    "run_update",
]
