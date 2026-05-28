"""Middleware pipeline for tool invocations.

Middlewares are simple callable wrappers around the underlying tool function
(both sync and coroutine variants). The default pipeline normalizes errors so
tools may raise :class:`ToolError` instead of returning ad-hoc strings.
"""

from agent.modules.tools.middleware.base import (
    ToolInvocationContext,
    apply_default_middleware,
    wrap_tool,
)
from agent.modules.tools.middleware.error_normalize import (
    error_normalization,
    error_normalization_async,
)
from agent.modules.tools.middleware.logging import (
    invocation_logging,
    invocation_logging_async,
)

__all__ = [
    "ToolInvocationContext",
    "apply_default_middleware",
    "error_normalization",
    "error_normalization_async",
    "invocation_logging",
    "invocation_logging_async",
    "wrap_tool",
]
