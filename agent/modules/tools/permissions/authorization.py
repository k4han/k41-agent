"""Shared tool adapter and execution boundary for permission checks."""

from contextlib import contextmanager
from contextvars import ContextVar

_executing = ContextVar("permission_execution", default=False)


@contextmanager
def permission_execution():
    """Prevent new permission interrupts after batch preparation."""
    token = _executing.set(True)
    try:
        yield
    finally:
        _executing.reset(token)


def authorize_tool(runtime, action, resource, *, risk=None, details=None,
                   allow_interrupt=True, context=None, validate_path=False, **metadata):
    from agent.modules.tools.coding.adapter import invocation_context
    from agent.modules.tools.coding.models import CodingError
    from agent.modules.tools.coding.service import get_coding_service
    from agent.modules.tools.result import ToolError, ToolErrorCode

    try:
        context = context or invocation_context(runtime)
        permissions = get_coding_service().permissions
        if validate_path:
            permissions.resolve_path(context, resource, action, authorize=False)
        permissions.assert_allowed(
            context, action, resource, risk=risk, details=details,
            allow_interrupt=allow_interrupt and not _executing.get(), **metadata)
    except CodingError as exc:
        code = ToolErrorCode.APPROVAL_UNAVAILABLE if exc.code == "approval_unavailable" else ToolErrorCode.PERMISSION_DENIED
        raise ToolError(code, str(exc), details={"code": exc.code}) from None
