"""Agent Client Protocol (ACP) client and agent runtime integration.

Other modules import from here. Attributes load on first use because this package
sits between the tools and workflow packages, which both depend on it lazily.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AGENT_DEFINITIONS": "agent.modules.acp.agents",
    "AcpAuthError": "agent.modules.acp.auth",
    "CallbackRejected": "agent.modules.acp.auth",
    "auth_status": "agent.modules.acp.auth",
    "AcpError": "agent.modules.acp.errors",
    "AcpNotInstalled": "agent.modules.acp.errors",
    "AcpRpcError": "agent.modules.acp.errors",
    "InstallBusyError": "agent.modules.acp.installer",
    "installed_binary": "agent.modules.acp.installer",
    "is_installed": "agent.modules.acp.installer",
    "get_acp_auth_manager": "agent.modules.acp.runtime",
    "get_acp_installer": "agent.modules.acp.runtime",
    "TurnRequest": "agent.modules.acp.service",
    "get_acp_service": "agent.modules.acp.service",
    "not_installed_notice": "agent.modules.acp.service",
    "peek_acp_service": "agent.modules.acp.service",
    "ToolOutput": "agent.modules.acp.tool_bridge",
    "tool_output": "agent.modules.acp.tool_bridge",
    "ACP_LIVE_EVENT": "agent.modules.acp.translator",
    "ACP_LIVE_KWARG": "agent.modules.acp.translator",
    "AcpTurn": "agent.modules.acp.turns",
    "PERMISSION_INTERRUPT_TYPE": "agent.modules.acp.turns",
    "PendingHandoff": "agent.modules.acp.turns",
    "SIGN_IN_NOTICE": "agent.modules.acp.turns",
    "STOP_TURN_OPTION_ID": "agent.modules.acp.turns",
    "describe_failure": "agent.modules.acp.turns",
    "permission_request_event": "agent.modules.acp.turns",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)


__all__ = sorted(_EXPORTS)
