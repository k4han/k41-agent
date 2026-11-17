"""Container-scoped access to the CLI authentication status probe."""

from __future__ import annotations

from typing import Any

from agent.modules.acp.auth import AcpAuthManager
from agent.modules.acp.service import get_acp_service


def get_acp_auth_manager(container: Any) -> AcpAuthManager:
    with container._lazy_lock:
        if container._acp_auth_manager is None:
            container._acp_auth_manager = AcpAuthManager(get_acp_service(container))
        return container._acp_auth_manager
