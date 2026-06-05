"""Short-lived metadata cache for cloud workspace backends."""

from __future__ import annotations

import copy
import time
from typing import Any

from agent.modules.workspaces.constants import METADATA_CACHE_TTL_SECONDS
from agent.modules.workspaces.refs import WorkspaceRef

MetadataCacheKey = tuple[str, str, str, str, tuple[Any, ...]]

_metadata_cache: dict[MetadataCacheKey, tuple[float, Any]] = {}


def _make_key(
    ref: WorkspaceRef,
    *,
    root: str,
    operation: str,
    parts: tuple[Any, ...],
) -> MetadataCacheKey:
    return (
        str(ref.backend),
        str(ref.locator),
        str(root or ""),
        str(operation),
        tuple(parts),
    )


def get_workspace_metadata_cache(
    ref: WorkspaceRef,
    *,
    root: str,
    operation: str,
    parts: tuple[Any, ...],
    ttl_seconds: float = METADATA_CACHE_TTL_SECONDS,
) -> tuple[bool, Any]:
    """Return cached metadata if it is still fresh."""
    key = _make_key(ref, root=root, operation=operation, parts=parts)
    cached = _metadata_cache.get(key)
    if cached is None:
        return False, None

    cached_at, value = cached
    if (time.monotonic() - cached_at) > ttl_seconds:
        _metadata_cache.pop(key, None)
        return False, None
    return True, copy.deepcopy(value)


def set_workspace_metadata_cache(
    ref: WorkspaceRef,
    value: Any,
    *,
    root: str,
    operation: str,
    parts: tuple[Any, ...],
) -> None:
    """Store metadata in the short-lived workspace cache."""
    key = _make_key(ref, root=root, operation=operation, parts=parts)
    _metadata_cache[key] = (time.monotonic(), copy.deepcopy(value))


def invalidate_workspace_metadata_cache(
    ref: WorkspaceRef,
    *,
    root: str | None = None,
) -> None:
    """Drop metadata cache entries for a workspace."""
    backend = str(ref.backend)
    locator = str(ref.locator)
    root_value = None if root is None else str(root)
    for key in list(_metadata_cache):
        key_backend, key_locator, key_root, _, _ = key
        if key_backend != backend or key_locator != locator:
            continue
        if root_value is not None and key_root != root_value:
            continue
        _metadata_cache.pop(key, None)


def clear_workspace_metadata_cache() -> None:
    """Clear all cached workspace metadata."""
    _metadata_cache.clear()


__all__ = [
    "clear_workspace_metadata_cache",
    "get_workspace_metadata_cache",
    "invalidate_workspace_metadata_cache",
    "set_workspace_metadata_cache",
]
