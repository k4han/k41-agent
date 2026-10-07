from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from agent.bootstrap.process_utils import spawn_detached_process
from agent.bootstrap.update import (
    DEFAULT_ARTIFACT_NAME,
    DEFAULT_OWNER,
    DEFAULT_REPO,
    UpdateError,
    detect_agent_home,
    fetch_latest_release,
    is_managed_install,
    is_systemd_service_active,
    is_version_newer,
    resolve_managed_install,
)
from agent.bootstrap.update_state import (
    UpdateBusyError,
    read_update_status,
    update_lock,
    write_update_status,
)
from agent.bootstrap.version import APP_VERSION

router = APIRouter()
logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600.0  # 1 hour
_cached_version_info: dict[str, Any] | None = None
_last_check_time: float = 0.0


@router.get("/dashboard-api/system/version")
def get_system_version(
    force: bool = Query(False, description="Force refresh from GitHub release API without using cache"),
) -> dict[str, Any]:
    global _cached_version_info, _last_check_time

    now = time.time()
    if not force and _cached_version_info is not None and (now - _last_check_time < CACHE_TTL_SECONDS):
        return _cached_version_info

    current_version = APP_VERSION
    managed = is_managed_install()
    install_type = "managed" if managed else "development"

    try:
        release = fetch_latest_release(
            owner=DEFAULT_OWNER,
            repo=DEFAULT_REPO,
            artifact_name=DEFAULT_ARTIFACT_NAME,
        )
        latest_version = release.version
        has_update = is_version_newer(latest_version, current_version)
        release_name = release.name or f"v{latest_version}"
        release_notes = release.body or ""
        release_url = release.html_url
        published_at = release.published_at or None
        error_msg = None
    except Exception as exc:
        logger.warning("Failed to check latest release from GitHub: %s", exc)
        latest_version = current_version
        has_update = False
        release_name = ""
        release_notes = ""
        release_url = ""
        published_at = None
        error_msg = str(exc)

    payload = {
        "current_version": current_version,
        "latest_version": latest_version,
        "has_update": has_update,
        "release_name": release_name,
        "release_notes": release_notes,
        "release_url": release_url,
        "published_at": published_at,
        "is_managed_install": managed,
        "install_type": install_type,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "error": error_msg,
    }

    if error_msg is None:
        _cached_version_info = payload
        _last_check_time = now

    return payload


@router.post("/dashboard-api/system/update")
def trigger_system_update(request: Request) -> dict[str, Any]:
    if not is_managed_install():
        raise HTTPException(
            status_code=400,
            detail=(
                "Automatic updates via the dashboard are only supported in managed installations. "
                "In development mode, please run 'git pull' and 'uv sync' from your terminal."
            ),
        )

    global _cached_version_info, _last_check_time
    _cached_version_info = None
    _last_check_time = 0.0

    try:
        install = resolve_managed_install()
        update_log = Path.home() / ".k41-agent" / "update.log"
        with update_lock(install.agent_home):
            previous = read_update_status(install.agent_home)
            if previous and previous.get("status") in {"queued", "running"}:
                raise UpdateBusyError("Another update is already in progress.")
            update_id = uuid4().hex
            command = [
                str(install.python_exe), "-m", "agent.bootstrap.cli", "update",
                "--yes", "--delay", "1.5", "--update-id", update_id,
            ]
            if is_systemd_service_active():
                systemd_run = shutil.which("systemd-run")
                if systemd_run is None:
                    raise RuntimeError("systemd-run is required to update a running systemd installation.")
                # A detached child still belongs to the server's cgroup. Use a separate scope.
                command = [systemd_run, "--user", "--scope", "--quiet", f"--unit=k41-agent-update-{update_id}", "--", *command]
            status = {"update_id": update_id, "status": "queued", "queued_at": time.time(), "error": None}
            write_update_status(install.agent_home, status)
            try:
                spawn_detached_process(command, update_log, cwd=install.agent_home)
            except Exception as exc:
                write_update_status(install.agent_home, {**status, "status": "failed", "error": str(exc)})
                raise
        return {
            "status": "started",
            "update_id": update_id,
            "message": "Update process initiated. The server will restart shortly.",
        }
    except UpdateBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to start update process: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate update process: {exc}",
        ) from exc


@router.get("/dashboard-api/system/update/status")
def get_system_update_status(update_id: str = Query(...)) -> dict[str, Any]:
    try:
        install = resolve_managed_install()
    except UpdateError as exc:
        # Startup failures can invalidate project metadata; the queued job remains readable.
        try:
            status = read_update_status(detect_agent_home())
        except UpdateError:
            status = None
        if status is None or status.get("update_id") != update_id:
            raise HTTPException(status_code=400, detail="Update status is only available for managed installations.") from exc
    else:
        status = read_update_status(install.agent_home)
    if status is None or status.get("update_id") != update_id:
        raise HTTPException(status_code=404, detail="Update status was not found.")
    return status
