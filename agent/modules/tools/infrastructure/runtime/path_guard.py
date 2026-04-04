"""Path safety utilities for tool filesystem access."""

from __future__ import annotations

import os


def resolve_safe_path(working_dir: str, file_path: str) -> str:
    """Resolve file_path within working_dir and block directory escapes."""
    real_base = os.path.realpath(working_dir)
    real_target = os.path.realpath(os.path.join(real_base, file_path))

    normalized_base = os.path.normcase(real_base)
    normalized_target = os.path.normcase(real_target)

    try:
        is_inside_base = (
            os.path.commonpath([normalized_base, normalized_target])
            == normalized_base
        )
    except ValueError as exc:
        raise ValueError(f"Path escapes working directory: {file_path}") from exc

    if not is_inside_base:
        raise ValueError(f"Path escapes working directory: {file_path}")

    return real_target