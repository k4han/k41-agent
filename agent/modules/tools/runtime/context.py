"""Runtime context utilities for tools."""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def get_context_value(runtime_or_context, key: str, default: T) -> T:
    """Safely extract a key from runtime.context.

    Supports both dict-like (TypedDict) and object-like contexts.
    """
    raw_context = runtime_or_context or {}
    if isinstance(raw_context, dict):
        return raw_context.get(key, default)
    return getattr(raw_context, key, default)
