"""Container-owned local browser automation."""

import logging

from agent.modules.browser.driver import CDPViewer, StagehandDriver
from agent.modules.browser.models import BrowserAccessDenied, BrowserAccountProfile, BrowserArtifact, BrowserBinding, BrowserHandoffResume, BrowserInvocation, BrowserProfile, BrowserProjectProfile
from agent.modules.browser.service import BrowserError, BrowserService

logger = logging.getLogger(__name__)

# Configurable key of the callable that waits for the user when no graph run can interrupt.
BROWSER_HANDOFF_WAIT_KEY = "browser_handoff_wait"


def get_browser_service(container=None):
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).browser_service


async def delete_thread_browsers(thread_id: str) -> None:
    """Delete persisted browser data even if no sessions were opened this process."""
    from agent.bootstrap.container import get_active_container

    container = get_active_container()
    if container is not None and container._async_session_maker is not None:
        await container.browser_service.delete_thread(thread_id)


async def release_delegated_browser(thread_id: str) -> None:
    """Release a finished sub-agent's browser tabs; delegation succeeds regardless."""
    from agent.bootstrap.container import get_active_container

    container = get_active_container()
    service = container._browser_service if container is not None else None
    if service is None:
        return
    try:
        await service.release_inherited_profile(thread_id)
    except Exception:
        logger.warning("A sub-agent's browser could not be released.", exc_info=True)


async def abandon_thread_handoffs(thread_id: str, *, expired_only: bool = False) -> None:
    """Release handoffs of a thread whose waiting tool call no longer exists."""
    from agent.bootstrap.container import get_active_container

    container = get_active_container()
    if container is not None and container._async_session_maker is not None:
        await container.browser_service.abandon_handoffs(thread_id, expired_only=expired_only)


__all__ = ["BROWSER_HANDOFF_WAIT_KEY", "BrowserAccessDenied", "BrowserAccountProfile", "BrowserArtifact", "BrowserBinding", "BrowserError", "BrowserHandoffResume",
           "BrowserInvocation", "BrowserProfile", "BrowserProjectProfile", "BrowserService", "CDPViewer", "StagehandDriver",
           "abandon_thread_handoffs", "get_browser_service", "delete_thread_browsers", "release_delegated_browser"]
