"""Container-owned local browser automation."""

from agent.modules.browser.driver import CDPViewer, StagehandDriver
from agent.modules.browser.models import BrowserArtifact, BrowserBinding, BrowserHandoffResume, BrowserInvocation, BrowserProfile
from agent.modules.browser.service import BrowserError, BrowserService


def get_browser_service(container=None):
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).browser_service


async def delete_thread_browsers(thread_id: str) -> None:
    """Delete persisted browser data even if no sessions were opened this process."""
    from agent.bootstrap.container import get_active_container

    container = get_active_container()
    if container is not None and container._async_session_maker is not None:
        await container.browser_service.delete_thread(thread_id)


__all__ = ["BrowserArtifact", "BrowserBinding", "BrowserError", "BrowserHandoffResume",
           "BrowserInvocation", "BrowserProfile", "BrowserService", "CDPViewer", "StagehandDriver", "get_browser_service",
           "delete_thread_browsers"]
