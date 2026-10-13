"""Error normalization middleware.

Preserves structured tool exceptions for BaseTool's error handling. This
keeps ToolMessage status accurate while unexpected errors receive a code.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import ToolException

from langgraph.errors import GraphInterrupt

from agent.modules.tools.result import (
    ToolError,
    ToolErrorCode,
)

logger = logging.getLogger(__name__)

def error_normalization(
    func: Callable[..., Any],
) -> Callable[..., Any]:
    """Sync error-normalization decorator."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except GraphInterrupt:
            raise
        except ToolError:
            raise
        except ToolException as exc:
            raise ToolError(ToolErrorCode.UPSTREAM, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error in tool %s", func.__name__)
            raise ToolError(ToolErrorCode.UNEXPECTED, str(exc) or exc.__class__.__name__) from exc

    return wrapper


def error_normalization_async(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Async error-normalization decorator."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except GraphInterrupt:
            raise
        except ToolError:
            raise
        except ToolException as exc:
            raise ToolError(ToolErrorCode.UPSTREAM, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error in tool %s", func.__name__)
            raise ToolError(ToolErrorCode.UNEXPECTED, str(exc) or exc.__class__.__name__) from exc

    return wrapper


__all__ = [
    "error_normalization",
    "error_normalization_async",
]
