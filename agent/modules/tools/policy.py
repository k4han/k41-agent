"""Per-agent tool policy.

A policy decides which tools an agent is allowed to invoke. The current model
mirrors :class:`AgentConfig`:

- ``tools``: explicit allow-list of tool names. ``None`` (only for ad-hoc
  configs, never for a parsed ``AgentConfig`` whose default is ``[]``)
  leaves names unrestricted; an empty list — including a missing ``tools``
  field which the parser defaults to ``[]`` — disables manually selected tools.
- ``agent_mcp_installs``: DB allow-list of MCP server names for the agent.
- ``sub_agents``: automatically enables ``delegate_task`` for configured targets.
  The resolver checks availability and ``delegate_task`` validates each invocation.
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
    allow_delegate_task: bool = True
    deny_tool_names: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_agent_config(cls, config) -> "ToolPolicy":
        """Build a policy from an :class:`AgentConfig`-shaped object."""
        tools = getattr(config, "tools", None)
        if tools is not None:
            from agent.modules.tools.coding.names import canonical_tool_names
            tools = canonical_tool_names(list(tools))
        sub_agents = getattr(config, "sub_agents", None)
        if tools is not None and sub_agents:
            tools = list(dict.fromkeys([*tools, "delegate_task"]))
        name = getattr(config, "name", "default")
        mcp_servers: list[str]
        try:
            from agent.modules.mcp import list_agent_mcp_server_names

            mcp_servers = list_agent_mcp_server_names(name)
        except Exception as exc:
            logger.warning(
                "Failed to load MCP bindings for agent %s; denying MCP access. %s",
                name,
                exc,
            )
            mcp_servers = []
        return cls(
            agent_name=name,
            allowed_tool_names=frozenset(tools) if tools is not None else None,
            allowed_mcp_servers=frozenset(mcp_servers),
            allow_delegate_task=bool(sub_agents),
        )

    @classmethod
    def allow_all(cls, agent_name: str = "default") -> "ToolPolicy":
        return cls(
            agent_name=agent_name,
            allowed_mcp_servers=None,
        )

    def is_allowed(self, descriptor: ToolDescriptor) -> bool:
        name = descriptor.name
        if name in self.deny_tool_names:
            return False
        if not self.allow_delegate_task and name == "delegate_task":
            return False
        if self.allowed_tool_names is not None and name not in self.allowed_tool_names:
            return False
        if descriptor.source is ToolSource.MCP and self.allowed_mcp_servers is not None:
            server = _extract_server_name(descriptor.name)
            if server not in self.allowed_mcp_servers:
                return False
        return True

    def filter(self, descriptors: Iterable[ToolDescriptor]) -> list[ToolDescriptor]:
        return [d for d in descriptors if self.is_allowed(d)]


__all__ = ["ToolPolicy"]
