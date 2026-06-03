"""Shared posix path utilities for sandbox backends."""

from __future__ import annotations

import posixpath


def normalize_posix_path(value: str) -> str:
    """Normalize a posix path string.

    Converts backslashes to forward slashes, applies posixpath.normpath,
    and strips trailing slashes (except for root ``/``).
    """
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raw = "."
    normalized = posixpath.normpath(raw)
    if normalized == ".":
        return ""
    return normalized.rstrip("/") or "/"


def resolve_remote_path(root: str, path: str) -> str:
    """Resolve *path* relative to *root* in a remote (posix) filesystem.

    Guards against directory traversal (``..``) escapes.
    """
    clean_root = normalize_posix_path(root) or "/"
    clean_path = str(path or "").strip()
    if not clean_path:
        return clean_root
    if clean_path == ".":
        return clean_root
    if not posixpath.isabs(clean_path):
        clean_path = posixpath.normpath(f"{clean_root}/{clean_path}")
    else:
        clean_path = posixpath.normpath(clean_path)
    if ".." in clean_path.split("/"):
        raise ValueError(f"Path escapes workspace: {path!r}")
    return normalize_posix_path(clean_path)


def relative_remote_path(root: str, target: str) -> str:
    """Return *target* relative to *root* in posix format.

    Returns an empty string when *target* equals *root* or escapes it.
    """
    target_path = normalize_posix_path(target)
    root_path = normalize_posix_path(root)
    if target_path == root_path:
        return ""
    if root_path == "/":
        return target_path.lstrip("/")
    if not target_path.startswith(f"{root_path}/"):
        raise ValueError("Path escapes workspace.")
    return target_path[len(root_path) + 1 :]


__all__ = [
    "normalize_posix_path",
    "relative_remote_path",
    "resolve_remote_path",
]
