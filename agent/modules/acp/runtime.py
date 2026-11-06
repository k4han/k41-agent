"""Container-scoped accessors for the installer and the auth manager."""

from __future__ import annotations

import asyncio
from typing import Any

from agent.modules.acp.auth import AcpAuthManager
from agent.modules.acp.installer import AcpInstaller
from agent.modules.acp.service import AcpService, get_acp_service


def _restart_after_install(service: AcpService, loop: asyncio.AbstractEventLoop, auth: AcpAuthManager, agent_id: str) -> None:
    """Runs on the install thread. Idle agents switch now; busy ones at their next start."""
    auth.invalidate(agent_id)

    async def restart() -> None:
        if not service.agent_is_busy(agent_id):
            await service.restart_client(agent_id)

    asyncio.run_coroutine_threadsafe(restart(), loop)


def get_acp_auth_manager(container: Any) -> AcpAuthManager:
    with container._lazy_lock:
        if container._acp_auth_manager is None:
            container._acp_auth_manager = AcpAuthManager(get_acp_service(container))
        return container._acp_auth_manager


def get_acp_installer(container: Any) -> AcpInstaller:
    """Create on first use. Requires a running loop, which every route handler has."""
    auth = get_acp_auth_manager(container)
    with container._lazy_lock:
        if container._acp_installer is None:
            service = get_acp_service(container)
            loop = asyncio.get_running_loop()
            container._acp_installer = AcpInstaller(
                on_installed=lambda agent_id: _restart_after_install(service, loop, auth, agent_id),
            )
        return container._acp_installer
