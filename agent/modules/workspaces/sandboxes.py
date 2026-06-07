"""High-level helpers for listing, stopping, archiving, and deleting cloud
sandboxes used by the Settings dashboard.

This module orchestrates the provider-specific backends (Daytona, Modal) and
the local ``thread_workspaces`` repository so the UI can render a unified
view of every cloud sandbox the agent knows about.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Any

from agent.modules.workspaces.refs import (
    DEFAULT_LOCAL_WORKSPACE,
    WorkspaceRef,
    normalize_workspace_ref,
)
from agent.modules.workspaces.registry import (
    DAYTONA_BACKEND,
    MODAL_BACKEND,
    call_workspace_backend_loader,
    get_workspace_backend_registry,
)
from agent.modules.workspaces.repository import get_thread_workspace_repository
from agent.modules.workspaces.service import _workspace_backend_uses_thread_loader

logger = logging.getLogger(__name__)


def thread_root_id(thread_id: str | None) -> str | None:
    normalized = str(thread_id or "").strip()
    if not normalized:
        return None
    return normalized.split(":sub:", 1)[0]


def _normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    if raw.startswith("sandboxstate."):
        raw = raw.rsplit(".", 1)[-1]
    if raw in {"running", "active"}:
        return "started"
    if raw in {"stopping", "stopped"}:
        return "stopped"
    if raw in {"archived", "archive"}:
        return "archived"
    if raw in {"destroyed", "deleted", "removed"}:
        return "destroyed"
    if raw in {"starting"}:
        return "starting"
    if raw in {"error", "build_failed"}:
        return "error"
    return raw


def _thread_workspace_payload(record: dict[str, Any]) -> dict[str, Any] | None:
    workspace = record.get("workspace")
    if not workspace:
        return None
    if not isinstance(workspace, dict):
        return None
    if not workspace.get("backend") or not workspace.get("locator"):
        return None
    return workspace


def _record_to_sandbox_summary(
    record: dict[str, Any],
    *,
    status_override: str | None = None,
    on_cloud: bool = True,
) -> dict[str, Any] | None:
    payload = _thread_workspace_payload(record)
    if payload is None:
        return None
    backend = str(payload.get("backend") or "").strip().lower()
    if backend not in {DAYTONA_BACKEND, MODAL_BACKEND}:
        return None
    sandbox_id = str(payload.get("locator") or "").strip()
    if not sandbox_id:
        return None
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    stored_status = str(metadata.get("status") or "").strip().lower()
    fallback_status = "started" if backend == MODAL_BACKEND else "unknown"
    status = _normalize_status(status_override or stored_status or fallback_status)
    repository_full_name = str(metadata.get("repository_full_name") or "").strip()
    label = str(payload.get("label") or sandbox_id).strip() or sandbox_id
    return {
        "sandbox_id": sandbox_id,
        "backend": backend,
        "label": label,
        "root": str(metadata.get("root") or "").strip(),
        "status": status,
        "thread_id": str(record.get("thread_id") or "").strip() or None,
        "repository_full_name": repository_full_name or None,
        "last_used_at": str(metadata.get("last_used_at") or "").strip() or None,
        "last_started_at": str(metadata.get("last_started_at") or "").strip() or None,
        "last_stopped_at": str(metadata.get("last_stopped_at") or "").strip() or None,
        "last_archived_at": str(metadata.get("last_archived_at") or "").strip() or None,
        "created_at": str(record.get("created_at") or "").strip() or None,
        "updated_at": str(record.get("updated_at") or "").strip() or None,
        "on_cloud": on_cloud,
        "is_orphan": False,
        "metadata": metadata,
    }


async def _daytona_sandboxes_from_thread_records() -> list[dict[str, Any]]:
    records = await get_thread_workspace_repository().list_by_backend(DAYTONA_BACKEND)
    return [
        summary
        for summary in (
            _record_to_sandbox_summary(record) for record in records.values()
        )
        if summary is not None
    ]


async def _modal_sandboxes_from_thread_records() -> list[dict[str, Any]]:
    records = await get_thread_workspace_repository().list_by_backend(MODAL_BACKEND)
    return [
        summary
        for summary in (
            _record_to_sandbox_summary(record) for record in records.values()
        )
        if summary is not None
    ]


def _daytona_sandboxes_from_cloud() -> list[dict[str, Any]]:
    """List Daytona sandboxes directly from the cloud provider."""
    descriptor = get_workspace_backend_registry().require(DAYTONA_BACKEND)
    if not descriptor.inventory_loader:
        return []
    lister = get_workspace_backend_registry().resolve_loader(
        DAYTONA_BACKEND,
        descriptor.inventory_loader,
    )
    return lister()


async def _modal_sandboxes_from_cloud() -> list[dict[str, Any]]:
    """List Modal sandboxes directly from the cloud provider."""
    descriptor = get_workspace_backend_registry().require(MODAL_BACKEND)
    if not descriptor.inventory_loader:
        return []
    lister = get_workspace_backend_registry().resolve_loader(
        MODAL_BACKEND,
        descriptor.inventory_loader,
    )
    result = lister()
    if inspect.isawaitable(result):
        return await result
    return result


def _merge_sandbox_lists(
    thread_records: list[dict[str, Any]],
    cloud_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine thread-attached records with cloud records.

    Thread records take precedence for metadata (label, repository, etc.) but
    the cloud record's ``status`` and ``on_cloud`` flag are kept because they
    reflect the live state.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for record in thread_records:
        by_id[record["sandbox_id"]] = record

    for cloud_record in cloud_records:
        sandbox_id = cloud_record["sandbox_id"]
        existing = by_id.get(sandbox_id)
        if existing is None:
            by_id[sandbox_id] = cloud_record
            continue
        merged = dict(existing)
        merged["status"] = cloud_record["status"]
        merged["on_cloud"] = cloud_record["on_cloud"]
        if not merged.get("last_used_at") and cloud_record.get("last_used_at"):
            merged["last_used_at"] = cloud_record["last_used_at"]
        if not merged.get("last_started_at") and cloud_record.get("last_started_at"):
            merged["last_started_at"] = cloud_record["last_started_at"]
        if not merged.get("last_stopped_at") and cloud_record.get("last_stopped_at"):
            merged["last_stopped_at"] = cloud_record["last_stopped_at"]
        by_id[sandbox_id] = merged

    return sorted(
        by_id.values(),
        key=lambda item: (
            0 if item.get("thread_id") else 1,
            -(parse_iso_timestamp(item.get("last_used_at")) or 0),
            item.get("sandbox_id") or "",
        ),
    )


def parse_iso_timestamp(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


async def list_sandboxes(
    backend: str,
    *,
    include_all: bool = False,
) -> dict[str, Any]:
    """List sandboxes for the given backend.

    Args:
        backend: ``"daytona"`` or ``"modal"``.
        include_all: When true, also include sandboxes found on the cloud
            provider that are not attached to any thread. When false, only
            sandboxes referenced from the ``thread_workspaces`` table are
            returned.
    """
    normalized = str(backend or "").strip().lower()
    if normalized not in {DAYTONA_BACKEND, MODAL_BACKEND}:
        raise ValueError(f"Unsupported sandbox backend: {backend!r}")

    if normalized == DAYTONA_BACKEND:
        thread_records = await _daytona_sandboxes_from_thread_records()
        if include_all:
            cloud_records = await asyncio.to_thread(_daytona_sandboxes_from_cloud)
        else:
            cloud_records = []
    else:
        thread_records = await _modal_sandboxes_from_thread_records()
        if include_all:
            cloud_records = await _modal_sandboxes_from_cloud()
        else:
            cloud_records = []

    sandboxes = _merge_sandbox_lists(thread_records, cloud_records)
    thread_ids = [
        root_id
        for root_id in (thread_root_id(entry.get("thread_id")) for entry in sandboxes)
        if root_id
    ]
    try:
        # Lazy import to break a circular dependency:
        # conversations.service -> agent_runtime -> workflows -> workspaces
        from agent.modules.conversations import list_active_thread_ids

        alive_ids = await list_active_thread_ids(thread_ids)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to resolve alive thread ids: %s", exc)
        alive_ids = set(thread_ids)
    for entry in sandboxes:
        root_id = thread_root_id(entry.get("thread_id"))
        entry["thread_alive"] = bool(root_id) and root_id in alive_ids
    return {
        "backend": normalized,
        "include_all": include_all,
        "count": len(sandboxes),
        "sandboxes": sandboxes,
    }


async def get_sandbox(backend: str, sandbox_id: str) -> dict[str, Any] | None:
    """Return a single sandbox summary by id, or ``None`` if not found."""
    normalized = str(backend or "").strip().lower()
    if normalized not in {DAYTONA_BACKEND, MODAL_BACKEND}:
        raise ValueError(f"Unsupported sandbox backend: {backend!r}")
    normalized_id = str(sandbox_id or "").strip()
    if not normalized_id:
        return None

    payload = await list_sandboxes(normalized, include_all=True)
    for entry in payload["sandboxes"]:
        if entry["sandbox_id"] == normalized_id:
            return entry
    return None


def _build_workspace_ref(backend: str, sandbox_id: str) -> WorkspaceRef:
    return normalize_workspace_ref(
        {"backend": backend, "locator": sandbox_id, "label": f"{backend}:{sandbox_id}"},
        default_locator=DEFAULT_LOCAL_WORKSPACE,
    )


async def _run_lifecycle_loader(
    backend: str,
    loader: str,
    ref: WorkspaceRef,
    *,
    in_thread: bool = False,
) -> Any:
    """Invoke a registered lifecycle loader, running blocking callables in a
    thread so the event loop is never stalled.
    """
    return await call_workspace_backend_loader(
        backend,
        loader,
        ref,
        in_thread=in_thread,
    )


async def stop_sandbox(backend: str, sandbox_id: str) -> str:
    normalized = str(backend or "").strip().lower()
    registry = get_workspace_backend_registry()
    descriptor = registry.require(normalized)
    if not descriptor.stop_loader:
        raise ValueError(f"Stop is not supported on {backend!r}.")
    ref = _build_workspace_ref(normalized, sandbox_id)
    return await _run_lifecycle_loader(
        normalized,
        descriptor.stop_loader,
        ref,
        in_thread=_workspace_backend_uses_thread_loader(normalized),
    )


async def archive_sandbox(backend: str, sandbox_id: str) -> str:
    normalized = str(backend or "").strip().lower()
    registry = get_workspace_backend_registry()
    descriptor = registry.require(normalized)
    if not descriptor.archive_loader:
        raise ValueError(f"Archive is not supported on {backend!r}.")
    ref = _build_workspace_ref(normalized, sandbox_id)
    return await _run_lifecycle_loader(
        normalized,
        descriptor.archive_loader,
        ref,
        in_thread=_workspace_backend_uses_thread_loader(normalized),
    )


async def delete_sandbox(backend: str, sandbox_id: str) -> dict[str, Any]:
    """Terminate the sandbox on the cloud provider and detach it from any
    threads that reference it.
    """
    normalized = str(backend or "").strip().lower()
    normalized_id = str(sandbox_id or "").strip()
    if not normalized_id:
        raise ValueError("Sandbox id is required.")

    registry = get_workspace_backend_registry()
    descriptor = registry.require(normalized)
    if not descriptor.delete_loader:
        raise ValueError(f"Delete is not supported on {backend!r}.")
    ref = _build_workspace_ref(normalized, normalized_id)
    cloud_status = await _run_lifecycle_loader(
        normalized,
        descriptor.delete_loader,
        ref,
        in_thread=_workspace_backend_uses_thread_loader(normalized),
    )

    detached_threads = await _detach_sandbox_from_threads(normalized, normalized_id)
    return {
        "status": "deleted",
        "backend": normalized,
        "sandbox_id": normalized_id,
        "cloud_status": cloud_status,
        "detached_threads": detached_threads,
    }


async def _detach_sandbox_from_threads(backend: str, sandbox_id: str) -> list[str]:
    """Remove the ``thread_workspaces`` rows that point at this sandbox.

    This only clears the *workspace assignment* for each affected thread so the
    agent knows the cloud sandbox is gone; the underlying conversation thread
    rows in ``conversation_threads`` are intentionally left untouched so the
    user keeps their chat history. Use ``mark_conversation_thread_deleted`` (or
    the conversations API) if the caller wants to actually delete a thread.
    """
    records = await get_thread_workspace_repository().list_by_backend(backend)
    detached: list[str] = []
    for thread_id, record in records.items():
        payload = _thread_workspace_payload(record)
        if payload is None:
            continue
        if str(payload.get("locator") or "").strip() != sandbox_id:
            continue
        deleted = await get_thread_workspace_repository().delete(thread_id)
        if deleted:
            detached.append(thread_id)
    return detached


__all__ = [
    "DAYTONA_BACKEND",
    "MODAL_BACKEND",
    "archive_sandbox",
    "delete_sandbox",
    "get_sandbox",
    "list_sandboxes",
    "stop_sandbox",
]
