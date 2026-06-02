"""Per-agent tool policy.

A policy decides which tools an agent is allowed to invoke. The current model
mirrors :class:`AgentConfig`:

- ``tools``: explicit allow-list of tool names. ``None``/empty means "all
  built-in tools are allowed".
- ``mcp_servers``: explicit allow-list of MCP server names (None means
  "all loaded MCP servers are allowed").
- ``sub_agents``: ``None`` disables ``call_agent``; otherwise the listed
  agents are permitted (validation happens inside ``call_agent`` itself).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from agent.modules.tools.domain import ToolDescriptor, ToolSource
from agent.modules.tools.sources.mcp import _extract_server_name


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
        mcp_servers = getattr(config, "mcp_servers", None)
        sub_agents = getattr(config, "sub_agents", None)
        name = getattr(config, "name", "default")
        return cls(
            agent_name=name,
            allowed_tool_names=frozenset(tools) if tools else None,
            allowed_mcp_servers=frozenset(mcp_servers) if mcp_servers is not None else None,
            allow_call_agent=sub_agents is not None,
            # Backward compat: the default chat agent auto-includes every
            # MCP tool so newly installed servers light up without editing
            # its allow-list.
            auto_include_all_mcp=(name == "default"),
        )

    @classmethod
    def allow_all(cls, agent_name: str = "default") -> "ToolPolicy":
        return cls(agent_name=agent_name)

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
        if self.allowed_tool_names is None:
            # No explicit allow-list: include every loaded MCP tool.
            return True
        if descriptor.name in self.allowed_tool_names:
            return True
        if self.auto_include_all_mcp:
            # Special compatibility flag: include every MCP tool even when an
            # allow-list is set (used by the default chat agent).
            return True
        return False

    def filter(self, descriptors: Iterable[ToolDescriptor]) -> list[ToolDescriptor]:
        return [d for d in descriptors if self.is_allowed(d)]


__all__ = ["ToolPolicy"]
