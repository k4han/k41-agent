"""Per-agent tool policy.

A policy decides which tools an agent is allowed to invoke. The current model
mirrors :class:`AgentConfig`:

- ``tools``: explicit allow-list of tool names. ``None``/empty means "all
  built-in tools are allowed".
- ``agent_mcp_installs``: DB allow-list of MCP server names for the agent.
- ``sub_agents``: ``None`` disables ``call_agent``; otherwise the listed
  agents are permitted (validation happens inside ``call_agent`` itself).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from agent.modules.tools.domain import ToolDescriptor, ToolSource
from agent.modules.tools.sources.mcp import _extract_server_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolPolicy:
    """Policy describing which tools an agent may use."""

    agent_name: str
    allowed_tool_names: frozenset[str] | None = None
    allowed_mcp_servers: frozenset[str] | None = None
    allow_call_agent: bool = True
    deny_tool_names: frozenset[str] = field(default_factory=frozenset)
    auto_include_all_mcp: bool = False

    @classmethod
    def from_agent_config(cls, config) -> "ToolPolicy":
        """Build a policy from an :class:`AgentConfig`-shaped object."""
        tools = getattr(config, "tools", None)
        sub_agents = getattr(config, "sub_agents", None)
        name = getattr(config, "name", "default")
        mcp_servers: list[str] | None
        try:
            from agent.modules.mcp import list_agent_mcp_server_names

            mcp_servers = list_agent_mcp_server_names(name)
        except Exception as exc:
            logger.warning(
                "Failed to load MCP bindings for agent %s; falling back to allow-all. %s",
                name,
                exc,
            )
            mcp_servers = None
        # Compatibility shim: the legacy "default" chat agent expected to
        # see every installed MCP tool. Preserve that behavior when no DB
        # bindings exist for the agent and the config did not pin a list.
        # An explicit empty list ``mcp_servers: []`` is treated the same
        # as ``None`` for this shim — the user has not opted into a list,
        # they have just left the field blank in YAML.
        raw_mcp_servers = getattr(config, "mcp_servers", None)
        config_pins_mcp = bool(raw_mcp_servers)
        if (
            name == "default"
            and not mcp_servers
            and not config_pins_mcp
        ):
            return cls(
                agent_name=name,
                allowed_tool_names=frozenset(tools) if tools else None,
                allowed_mcp_servers=None,
                allow_call_agent=sub_agents is not None,
                auto_include_all_mcp=True,
            )
        return cls(
            agent_name=name,
            allowed_tool_names=frozenset(tools) if tools else None,
            allowed_mcp_servers=frozenset(mcp_servers) if mcp_servers is not None else None,
            allow_call_agent=sub_agents is not None,
            auto_include_all_mcp=False,
        )

    @classmethod
    def allow_all(cls, agent_name: str = "default") -> "ToolPolicy":
        return cls(
            agent_name=agent_name,
            allowed_mcp_servers=None,
            auto_include_all_mcp=True,
        )

    def is_allowed(self, descriptor: ToolDescriptor) -> bool:
        name = descriptor.name
        if name in self.deny_tool_names:
            return False
        if not self.allow_call_agent and name == "call_agent":
            return False
        if descriptor.source is ToolSource.MCP:
            return self._is_mcp_allowed(descriptor)
        return self._is_local_allowed(name)

    def _is_local_allowed(self, name: str) -> bool:
        if self.allowed_tool_names is None:
            return True
        # MCP tool names are also accepted in allow-list for compatibility
        return name in self.allowed_tool_names

    def _is_mcp_allowed(self, descriptor: ToolDescriptor) -> bool:
        if self.allowed_mcp_servers is not None:
            server = _extract_server_name(descriptor.name)
            if server not in self.allowed_mcp_servers:
                return False
            # An explicit server list on its own opts the agent into every
            # tool from those servers.
            return True
        # ``allowed_mcp_servers is None`` means "no MCP filter": allow every
        # loaded MCP tool. ``auto_include_all_mcp`` extends this to agents
        # that set a built-in tool allow-list (e.g. legacy ``default``).
        if self.allowed_tool_names is None or self.auto_include_all_mcp:
            return True
        return descriptor.name in self.allowed_tool_names

    def filter(self, descriptors: Iterable[ToolDescriptor]) -> list[ToolDescriptor]:
        return [d for d in descriptors if self.is_allowed(d)]


__all__ = ["ToolPolicy"]
