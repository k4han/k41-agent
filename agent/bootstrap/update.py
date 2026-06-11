from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile
import tomllib
from typing import Callable

import httpx

from agent.bootstrap.version import APP_VERSION, PACKAGE_NAME


DEFAULT_OWNER = "k4han"
DEFAULT_REPO = "k41-agent"
DEFAULT_ARTIFACT_NAME = "k41-agent-release.zip"
BACKUP_PREFIX = "app-"
DOWNLOAD_TIMEOUT_SECONDS = 60.0
SERVER_STOP_TIMEOUT_SECONDS = 15.0
SERVER_LOG_FILE = Path.home() / ".k41-agent" / "server.log"
PID_FILE = Path.home() / ".k41-agent" / "server.pid"
SHUTDOWN_SIGNAL = Path.home() / ".k41-agent" / "shutdown.signal"


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
    echo = echo or (lambda message: None)
    confirm = confirm or (lambda message: True)

    install: ManagedInstall | None = None
    current_version = options.current_version or APP_VERSION
    if not options.check_only:
        install = resolve_managed_install(
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

    running_pid = get_running_server_pid()
    should_restart = running_pid is not None
    if running_pid is not None:
        echo(f"Stopping running server (PID {running_pid})")
        stop_running_server(running_pid)

    backup_path: Path | None = None
    try:
        backup_path = backup_app_source(install, current_version)
        prune_backups(install.backup_dir, keep=backup_path)
        echo(f"Backup created at {backup_path}")

        replace_app_source(install, source_root)
        sync_app(install)
        initialize_app(install)
    except Exception as exc:
        if backup_path is not None:
            echo("Update failed. Restoring previous source from backup.")
            restore_app_source(install, backup_path)
            try:
                sync_app(install)
            except Exception as rollback_exc:
                raise UpdateError(
                    "Update failed and rollback dependency sync failed: "
                    f"{rollback_exc}"
                ) from exc
        if should_restart:
            start_server(install)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Update failed: {exc}") from exc

    if should_restart:
        echo("Restarting server")
        start_server(install)

    cleanup_downloads(install)
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


def detect_agent_home(*, executable: str | None = None) -> Path:
    for env_name in ("K41_AGENT_HOME", "AGENT_HOME"):
        value = os.environ.get(env_name)
        if value:
            return Path(value).expanduser().resolve()

    executable_path = Path(executable or sys.executable).resolve()
    for parent in executable_path.parents:
        if parent.name.lower() == "envs":
            return parent.parent.resolve()

    raise UpdateError(
        "Could not determine AGENT_HOME. Run updates from an installed K41 Agent."
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
    owner: str,
    repo: str,
    artifact_name: str,
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
    )


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
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
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
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if not is_process_alive(pid) or not is_k41_process(pid):
        return None
    return pid


def stop_running_server(pid: int) -> None:
    SHUTDOWN_SIGNAL.parent.mkdir(parents=True, exist_ok=True)
    SHUTDOWN_SIGNAL.write_text(str(pid), encoding="utf-8")
    deadline = time.time() + SERVER_STOP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if not is_process_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            SHUTDOWN_SIGNAL.unlink(missing_ok=True)
            return
        time.sleep(0.5)
    raise UpdateError(f"Server process {pid} did not stop within timeout.")


def start_server(install: ManagedInstall) -> None:
    env = os.environ.copy()
    env["K41_DAEMONIZED"] = "1"
    SERVER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SERVER_LOG_FILE.open("ab") as log_file:
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess.Popen(
                [str(install.python_exe), "-m", "agent.bootstrap.cli"],
                cwd=install.app_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                startupinfo=startupinfo,
                close_fds=True,
            )
            return
        subprocess.Popen(
            [str(install.python_exe), "-m", "agent.bootstrap.cli"],
            cwd=install.app_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


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


def is_k41_process(pid: int) -> bool:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                capture_output=True,
                text=True,
            )
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True,
                text=True,
            )
        output = result.stdout.lower()
        return "k41" in output or "agent.bootstrap.cli" in output
    except Exception:
        return False


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
    "ManagedInstall",
    "ReleaseInfo",
    "UpdateError",
    "UpdateOptions",
    "UpdateResult",
    "detect_agent_home",
    "extract_release_artifact",
    "fetch_latest_release",
    "is_version_newer",
    "read_project_metadata",
    "resolve_managed_install",
    "run_update",
]
