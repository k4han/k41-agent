"""Base types and wrapping helpers for tool middleware."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool

MIDDLEWARE_APPLIED_ATTR = "__k41_middleware_applied__"


def wrap_tool(
    tool_obj: BaseTool,
    *,
    sync_middlewares: list[Callable[[Callable[..., Any]], Callable[..., Any]]] | None = None,
    async_middlewares: list[Callable[[Callable[..., Any]], Callable[..., Any]]] | None = None,
) -> BaseTool:
    """Apply middleware decorators to a tool's ``func``/``coroutine``.

    Each middleware is a function ``decorator(callable) -> callable``. The
    outermost middleware is applied last. ``wrap_tool`` is idempotent: a tool
    already wrapped will not be wrapped again.
    """
    if getattr(tool_obj, MIDDLEWARE_APPLIED_ATTR, False):
        return tool_obj

    sync_middlewares = sync_middlewares or []
    async_middlewares = async_middlewares or []

    func = getattr(tool_obj, "func", None)
    if callable(func) and not inspect.iscoroutinefunction(func):
        wrapped = func
        for mw in sync_middlewares:
            wrapped = mw(wrapped)
        tool_obj.func = wrapped  # type: ignore[attr-defined]

    coroutine = getattr(tool_obj, "coroutine", None)
    if callable(coroutine) and inspect.iscoroutinefunction(coroutine):
        wrapped_co = coroutine
        for mw in async_middlewares:
            wrapped_co = mw(wrapped_co)
        tool_obj.coroutine = wrapped_co  # type: ignore[attr-defined]

    setattr(tool_obj, MIDDLEWARE_APPLIED_ATTR, True)
    return tool_obj


def apply_default_middleware(tool_obj: BaseTool) -> BaseTool:
    """Apply the project's default middleware chain.

    Currently this is error normalization only; logging stays opt-in because
    callers may want to control structured-log formatting themselves.
    """
    # imported lazily to avoid a circular import on package load
    from agent.modules.tools.middleware.error_normalize import (
        error_normalization,
        error_normalization_async,
    )

    from agent.modules.tools.result import format_tool_error

    tool_obj.handle_tool_error = format_tool_error
    tool_obj.handle_validation_error = lambda exc: f"[error] invalid_input: {exc}"

    return wrap_tool(
        tool_obj,
        sync_middlewares=[
            error_normalization,
        ],
        async_middlewares=[
            error_normalization_async,
        ],
    )


__all__ = [
    "MIDDLEWARE_APPLIED_ATTR",
    "apply_default_middleware",
    "wrap_tool",
]
