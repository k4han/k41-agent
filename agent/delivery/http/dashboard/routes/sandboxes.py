"""HTTP routes exposing cloud sandbox inventory and lifecycle actions.

These endpoints back the ``/settings/sandboxes`` dashboard page and only
operate on the cloud-capable backends (Daytona, Modal). ``local`` is not
listed because there is no remote resource to manage.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from agent.modules.workspaces.sandboxes import (
    DAYTONA_BACKEND,
    MODAL_BACKEND,
    archive_sandbox,
    delete_sandbox,
    get_sandbox,
    list_sandboxes,
    stop_sandbox,
)


logger = logging.getLogger(__name__)


router = APIRouter()


SUPPORTED_BACKENDS = {DAYTONA_BACKEND, MODAL_BACKEND}


def _normalize_backend(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPORTED_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported sandbox backend: {value!r}. "
                f"Supported values: {sorted(SUPPORTED_BACKENDS)}."
            ),
        )
    return normalized


@router.get("/dashboard-api/sandboxes")
async def list_sandboxes_endpoint(
    backend: str = Query(default=DAYTONA_BACKEND, min_length=1),
    include_all: bool = Query(default=False),
) -> dict[str, Any]:
    """List sandboxes known to the agent for a given backend.

    Args:
        backend: ``daytona`` or ``modal``.
        include_all: When true, also return cloud sandboxes that are not
            attached to any conversation thread.
    """
    normalized = _normalize_backend(backend)
    try:
        return await list_sandboxes(normalized, include_all=include_all)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to list sandboxes for %s", normalized)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list sandboxes: {exc}",
        ) from exc


@router.get("/dashboard-api/sandboxes/{backend}/{sandbox_id}")
async def get_sandbox_endpoint(backend: str, sandbox_id: str) -> dict[str, Any]:
    normalized = _normalize_backend(backend)
    summary = await get_sandbox(normalized, sandbox_id)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sandbox {sandbox_id!r} not found for backend {normalized!r}.",
        )
    return summary


@router.delete("/dashboard-api/sandboxes/{backend}/{sandbox_id}")
async def delete_sandbox_endpoint(backend: str, sandbox_id: str) -> dict[str, Any]:
    normalized = _normalize_backend(backend)
    if not sandbox_id.strip():
        raise HTTPException(status_code=400, detail="Sandbox id is required.")
    try:
        result = await delete_sandbox(normalized, sandbox_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete sandbox %s/%s", normalized, sandbox_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete sandbox: {exc}",
        ) from exc
    return result


@router.post("/dashboard-api/sandboxes/{backend}/{sandbox_id}/stop")
async def stop_sandbox_endpoint(backend: str, sandbox_id: str) -> dict[str, Any]:
    normalized = _normalize_backend(backend)
    if not sandbox_id.strip():
        raise HTTPException(status_code=400, detail="Sandbox id is required.")
    try:
        status = await stop_sandbox(normalized, sandbox_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to stop sandbox %s/%s", normalized, sandbox_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop sandbox: {exc}",
        ) from exc
    return {
        "status": "stopped",
        "backend": normalized,
        "sandbox_id": sandbox_id,
        "cloud_status": status,
    }


@router.post("/dashboard-api/sandboxes/{backend}/{sandbox_id}/archive")
async def archive_sandbox_endpoint(backend: str, sandbox_id: str) -> dict[str, Any]:
    normalized = _normalize_backend(backend)
    if not sandbox_id.strip():
        raise HTTPException(status_code=400, detail="Sandbox id is required.")
    try:
        status = await archive_sandbox(normalized, sandbox_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to archive sandbox %s/%s", normalized, sandbox_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to archive sandbox: {exc}",
        ) from exc
    return {
        "status": "archived",
        "backend": normalized,
        "sandbox_id": sandbox_id,
        "cloud_status": status,
    }


__all__ = [
    "DAYTONA_BACKEND",
    "MODAL_BACKEND",
    "SUPPORTED_BACKENDS",
    "router",
]
