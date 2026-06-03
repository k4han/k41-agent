"""Shared file-info helpers for sandbox backends."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent.modules.workspaces.posix_utils import normalize_posix_path

import posixpath


def file_info_name(item: Any) -> str:
    """Extract the file/directory name from a sandbox file-info object."""
    raw_name = getattr(item, "name", "")
    if not raw_name:
        path_value = str(getattr(item, "path", "") or "").strip()
        raw_name = path_value.rsplit("/", 1)[-1]
    return str(raw_name or "").strip().rstrip("/")


def file_info_is_dir(item: Any) -> bool:
    """Return ``True`` if the file-info object represents a directory."""
    value = getattr(item, "is_dir", None)
    if value is None:
        value = getattr(item, "is_directory", None)
    if value is not None:
        return bool(value)
    # Fallback: check a type attribute (Modal-style)
    info_type = _file_info_type(item)
    return info_type in {"dir", "directory"}


def file_info_size(item: Any) -> int:
    """Return the file size in bytes from a sandbox file-info object."""
    try:
        return int(getattr(item, "size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def file_info_modified_at(item: Any) -> float:
    """Return the last-modified timestamp (epoch seconds) from a file-info object."""
    value = getattr(item, "modified_time", None)
    if value is None:
        value = getattr(item, "mod_time", None)
    if value is None:
        value = getattr(item, "modified_at", None)
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        try:
            return float(timestamp())
        except Exception:
            return 0.0
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def file_info_path(item: Any, parent: str) -> str:
    """Return the absolute posix path of *item* inside *parent*."""
    path_value = str(getattr(item, "path", "") or "").strip()
    if path_value:
        return normalize_posix_path(path_value)
    name = file_info_name(item)
    if name == parent or name.startswith(f"{parent}/") or posixpath.isabs(name):
        return normalize_posix_path(name)
    return normalize_posix_path(posixpath.join(parent, name))


def _file_info_type(item: Any) -> str:
    """Return the lowercase type name from a file-info object."""
    value = getattr(item, "type", None)
    name = getattr(value, "name", None)
    return str(name or value or "").strip().lower()


__all__ = [
    "file_info_is_dir",
    "file_info_modified_at",
    "file_info_name",
    "file_info_path",
    "file_info_size",
]
