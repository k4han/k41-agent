"""Logging middleware for tool invocations.

Opt-in; not part of the default chain. Useful for trace-level diagnostics
when chasing a misbehaving tool.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


def invocation_logging(func: Callable[..., Any]) -> Callable[..., Any]:
    """Log entry, duration, and exit for a sync tool function."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        name = getattr(func, "__name__", "<tool>")
        start = time.perf_counter()
        logger.debug("Tool %s invoked", name)
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.debug("Tool %s finished in %.2fms", name, elapsed_ms)

    return wrapper


def invocation_logging_async(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Log entry, duration, and exit for an async tool coroutine."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        name = getattr(func, "__name__", "<tool>")
        start = time.perf_counter()
        logger.debug("Tool %s invoked", name)
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.debug("Tool %s finished in %.2fms", name, elapsed_ms)

    return wrapper


__all__ = [
    "invocation_logging",
    "invocation_logging_async",
]
