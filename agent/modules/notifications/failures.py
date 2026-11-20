"""Report observable background and sandbox failures at their execution boundaries."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from agent.modules.notifications.interruption import active_manager

logger = logging.getLogger(__name__)
_background: ContextVar[tuple[str, str, str] | None] = ContextVar(
    "background_notification_context", default=None,
)


@contextmanager
def background_scope(key, source, thread_id):
    token = _background.set((key, source, thread_id))
    try:
        yield
    finally:
        _background.reset(token)


async def report_failure(key, source, thread_id, *, error=None, rate=False, destination=None) -> bool:
    manager = active_manager()
    if manager is None:
        return False
    try:
        recipients = await manager.publish_error(key, source, thread_id, error=error, rate=rate)
        return destination in recipients
    except Exception:
        logger.warning("Could not enqueue an execution failure notification.", exc_info=True)
        return False


async def report_provider_limit(error):
    scope = _background.get()
    if scope:
        key, source, thread_id = scope
        await report_failure(key, source, thread_id, error=error, rate=True)
