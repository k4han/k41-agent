"""Per-agent tool resolver.

Combines the unified registry with a :class:`ToolPolicy` to return the exact
set of tools an agent may use at runtime.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from langchain_core.tools import BaseTool

from agent.modules.tools.config import materialize_tool
from agent.modules.tools.domain import ToolDescriptor
from agent.modules.tools.policy import ToolPolicy
from agent.modules.tools.registry_service import (
    ensure_mcp_loaded,
    get_registry_service,
)


class ToolResolver:
    """Resolve tools for a given agent according to its policy."""

    def __init__(self, *, include_mcp: bool = True) -> None:
        self._include_mcp = include_mcp

    def for_workspace(self, tools: list[BaseTool], workspace, agent_name: str = "default") -> list[BaseTool]:
        """Use the same coding definitions on every workspace backend."""
        from agent.modules.tools.coding.adapter import make_coding_tool
        from agent.modules.tools.coding.names import CODING_TOOLS, TOOL_ALIASES, REMOVED_TOOLS
        from agent.modules.workspaces import normalize_workspace_ref
        from agent.shared.config.constants import DEFAULT_WORKSPACE_ROOT
        normalize_workspace_ref(workspace, default_locator=DEFAULT_WORKSPACE_ROOT)
        result = []
        for tool in tools:
            if tool.name in REMOVED_TOOLS:
                continue
            name = TOOL_ALIASES.get(tool.name, tool.name)
            result.append(make_coding_tool(name) if name in CODING_TOOLS else tool)
        control_tools = {"ask_user", "plan_mode_respond", "write_todos"}
        if result and not any(tool.name == "read" for tool in result) and any(tool.name not in control_tools for tool in result):
            result.append(make_coding_tool("read", retained_only=True))
        return list({tool.name: tool for tool in result}.values())

    async def aresolve_for_agent(
        self,
        agent_name: str,
        *,
        override_tool_names: Iterable[str] | None = None,
    ) -> list[BaseTool]:
        """Async resolution that pulls in MCP tools as well.

        ``override_tool_names``: when provided, replaces the agent config's
        ``tools`` allow-list (used by sub-agent calls that explicitly scope
        the toolset for the runtime).
        """
        if self._include_mcp:
            await ensure_mcp_loaded()
        return self._resolve_for_agent_sync(
            agent_name, override_tool_names=override_tool_names
        )

    def resolve_for_agent(
        self,
        agent_name: str,
        *,
        override_tool_names: Iterable[str] | None = None,
    ) -> list[BaseTool]:
        """Sync resolution. Returns only built-in tools unless MCP was already
        loaded explicitly via ``ensure_mcp_loaded``.
        """
        return self._resolve_for_agent_sync(
            agent_name, override_tool_names=override_tool_names
        )

    def resolve_for_policy(
        self, policy: ToolPolicy
    ) -> list[ToolDescriptor]:
        service = get_registry_service()
        return policy.filter(service.get_descriptors())

    def _resolve_for_agent_sync(
        self,
        agent_name: str,
        *,
        override_tool_names: Iterable[str] | None = None,
    ) -> list[BaseTool]:
        config = self._load_agent_config(agent_name)
        policy = (
            ToolPolicy.from_agent_config(config)
            if config is not None
            else ToolPolicy.allow_all(agent_name=agent_name or "default")
        )
        if override_tool_names is not None:
            from agent.modules.tools.coding.names import canonical_tool_names
            override_set = frozenset(canonical_tool_names(override_tool_names))
            policy = replace(
                policy,
                allowed_tool_names=override_set if override_set else None,
            )
        agent_tool_configs = (
            getattr(config, "tool_configs", None)
            if config is not None
            else None
        )
        return [
            materialize_tool(d, agent_tool_configs)
            for d in self.resolve_for_policy(policy)
        ]

    @staticmethod
    def _load_agent_config(agent_name: str):
        # local import to avoid a circular dependency between agents and tools
        from agent.modules.agents import get_catalog_service

        catalog = get_catalog_service()
        return catalog.get_agent(agent_name) if agent_name else None


__all__ = ["ToolResolver"]
