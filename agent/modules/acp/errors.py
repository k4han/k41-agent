"""Error types raised by the ACP client."""

from __future__ import annotations

from typing import Any

AUTH_REQUIRED_CODE = -32000
METHOD_NOT_FOUND_CODE = -32601


class AcpError(Exception):
    """Base class for ACP failures."""


class AcpConnectionClosed(AcpError):
    """The agent process or its pipes went away."""


class AcpRpcError(AcpError):
    """The agent answered a request with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    @property
    def auth_required(self) -> bool:
        return self.code == AUTH_REQUIRED_CODE and "authentication required" in self.message.lower()


class AcpNotInstalled(AcpError):
    """The agent binary has not been installed."""
