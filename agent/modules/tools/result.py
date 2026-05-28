"""Unified error type and formatting for tool execution.

Tools should raise ``ToolError`` instead of returning ad-hoc strings like
``"[Error] ..."``. A middleware layer is responsible for catching these
exceptions and producing a consistent textual representation for the LLM.
"""

from __future__ import annotations

from enum import StrEnum


class ToolErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    UNEXPECTED = "unexpected"


class ToolError(Exception):
    """Structured error raised by tools."""

    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details) if details else {}

    def __str__(self) -> str:
        return self.message

    def to_string(self) -> str:
        """Render the error in the canonical wire format."""
        return format_tool_error(self)


def format_tool_error(error: ToolError) -> str:
    """Canonical textual format returned to the LLM."""
    return f"[error] {error.code.value}: {error.message}"


__all__ = [
    "ToolError",
    "ToolErrorCode",
    "format_tool_error",
]
