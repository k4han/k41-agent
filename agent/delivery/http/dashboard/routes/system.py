from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from agent.bootstrap.process_utils import spawn_detached_process
from agent.bootstrap.update import (
    DEFAULT_ARTIFACT_NAME,
    DEFAULT_OWNER,
    DEFAULT_REPO,
    fetch_latest_release,
    is_managed_install,
    is_version_newer,
    resolve_managed_install,
)
from agent.bootstrap.version import APP_VERSION

router = APIRouter()
logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600.0  # 1 hour
_cached_version_info: dict[str, Any] | None = None
_last_check_time: float = 0.0


@router.get("/dashboard-api/system/version")
async def get_system_version(
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
async def trigger_system_update(request: Request) -> dict[str, Any]:
    if not is_managed_install():
        raise HTTPException(
            status_code=400,
            detail=(
                "Automatic updates via the dashboard are only supported in managed installations. "
                "In development mode, please run 'git pull' and 'uv sync' from your terminal."
            ),
        )

    try:
        install = resolve_managed_install()
        update_log = Path.home() / ".k41-agent" / "update.log"
        spawn_detached_process(
            [str(install.python_exe), "-m", "agent.bootstrap.cli", "update", "--yes", "--delay", "1.5"],
            update_log,
            cwd=install.app_dir,
        )
        return {
            "status": "started",
            "message": "Update process initiated. The server will restart shortly.",
        }
    except Exception as exc:
        logger.error("Failed to start update process: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate update process: {exc}",
        ) from exc
