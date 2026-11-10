"""Typed errors raised by the Instabox REST client."""

from __future__ import annotations

from typing import Any

AUTH_ERROR_CODES = frozenset(
    {
        "missing_key",
        "malformed_key",
        "invalid_key",
        "expired_key",
        "revoked_key",
        "session_expired",
        "missing_scope",
        "permission_denied",
    }
)
NOT_FOUND_ERROR_CODES = frozenset(
    {"machine_not_found", "snapshot_not_found", "operation_not_found", "not_found"}
)


class InstaboxError(RuntimeError):
    """Base class for Instabox integration failures."""


class InstaboxDisabledError(InstaboxError):
    """The Instabox backend is disabled or missing credentials."""


class InstaboxConfirmationError(InstaboxError, ValueError):
    """A destructive action was requested without a matching confirmation."""


class InstaboxConflictError(InstaboxError):
    """A local guard (desktop control, restore lock, running task) refused the action."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class InstaboxApiError(InstaboxError):
    """An error response returned by the Instabox API.

    Callers must branch on ``code``; ``message`` is for humans only.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str = "",
        retryable: bool = False,
        retry_after: float | None = None,
        operation_id: str | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code or ""
        self.retryable = retryable
        self.retry_after = retry_after
        self.operation_id = operation_id
        self.request_id = request_id
        self.details = details or {}

    @property
    def is_not_found(self) -> bool:
        return self.code in NOT_FOUND_ERROR_CODES or (self.status == 404 and not self.code)

    @property
    def is_auth_error(self) -> bool:
        return self.code in AUTH_ERROR_CODES or self.status in {401, 403}

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "retry_after": self.retry_after,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
        }


def is_instabox_not_found(exc: BaseException) -> bool:
    return isinstance(exc, InstaboxApiError) and exc.is_not_found


def is_instabox_auth_error(exc: BaseException) -> bool:
    return isinstance(exc, InstaboxApiError) and exc.is_auth_error


def is_instabox_transient(exc: BaseException) -> bool:
    if isinstance(exc, InstaboxApiError):
        return exc.retryable or exc.status >= 500 or exc.status == 429
    return False


__all__ = [
    "AUTH_ERROR_CODES",
    "InstaboxApiError",
    "InstaboxConfirmationError",
    "InstaboxConflictError",
    "InstaboxDisabledError",
    "InstaboxError",
    "NOT_FOUND_ERROR_CODES",
    "is_instabox_auth_error",
    "is_instabox_not_found",
    "is_instabox_transient",
]
