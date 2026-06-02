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
from typing import Any, Literal

from agent.modules.tools.result import (
    ToolError,
    ToolErrorCode,
    format_tool_error,
)

logger = logging.getLogger(__name__)

ToolResponseFormat = Literal["content", "content_and_artifact"]


def _format_error_result(error: ToolError, response_format: str) -> Any:
    content = format_tool_error(error)
    if response_format == "content_and_artifact":
        return content, None
    return content


def error_normalization(
    func: Callable[..., Any] | None = None,
    *,
    response_format: ToolResponseFormat = "content",
) -> Callable[..., Any]:
    """Sync error-normalization decorator."""

    if func is None:
        return functools.partial(error_normalization, response_format=response_format)

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ToolError as exc:
            return _format_error_result(exc, response_format)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error in tool %s", func.__name__)
            return _format_error_result(
                ToolError(ToolErrorCode.UNEXPECTED, str(exc) or exc.__class__.__name__),
                response_format,
            )

    return wrapper


def error_normalization_async(
    func: Callable[..., Awaitable[Any]] | None = None,
    *,
    response_format: ToolResponseFormat = "content",
) -> Callable[..., Awaitable[Any]]:
    """Async error-normalization decorator."""

    if func is None:
        return functools.partial(
            error_normalization_async,
            response_format=response_format,
        )

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except ToolError as exc:
            return _format_error_result(exc, response_format)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error in tool %s", func.__name__)
            return _format_error_result(
                ToolError(ToolErrorCode.UNEXPECTED, str(exc) or exc.__class__.__name__),
                response_format,
            )

    return wrapper


__all__ = [
    "ToolResponseFormat",
    "error_normalization",
    "error_normalization_async",
]
