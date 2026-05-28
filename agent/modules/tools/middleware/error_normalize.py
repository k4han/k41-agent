"""Error normalization middleware.

Wraps a tool function so any :class:`ToolError` is returned as the canonical
``[error] code: message`` string instead of bubbling up. Unhandled exceptions
are caught and converted to ``UNEXPECTED`` errors so the LLM gets a useful
message and the application keeps running.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent.modules.tools.result import (
    ToolError,
    ToolErrorCode,
    format_tool_error,
)

logger = logging.getLogger(__name__)


def error_normalization(func: Callable[..., Any]) -> Callable[..., Any]:
    """Sync error-normalization decorator."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ToolError as exc:
            return format_tool_error(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error in tool %s", func.__name__)
            return format_tool_error(
                ToolError(ToolErrorCode.UNEXPECTED, str(exc) or exc.__class__.__name__)
            )

    return wrapper


def error_normalization_async(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Async error-normalization decorator."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except ToolError as exc:
            return format_tool_error(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error in tool %s", func.__name__)
            return format_tool_error(
                ToolError(ToolErrorCode.UNEXPECTED, str(exc) or exc.__class__.__name__)
            )

    return wrapper


__all__ = [
    "error_normalization",
    "error_normalization_async",
]
