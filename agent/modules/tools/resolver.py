"""Per-agent tool resolver.

Combines the unified registry with a :class:`ToolPolicy` to return the exact
set of tools an agent may use at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import replace

from langchain_core.tools import BaseTool

from agent.modules.tools.config import materialize_tool
from agent.modules.tools.domain import ToolDescriptor, ToolSource
from agent.modules.tools.policy import ToolPolicy
from agent.modules.tools.registry_service import (
    ensure_mcp_loaded,
    get_registry_service,
)

logger = logging.getLogger(__name__)


class ToolResolver:
    """Resolve tools for a given agent according to its policy."""

    def __init__(self, *, include_mcp: bool = True) -> None:
        self._include_mcp = include_mcp

    def for_workspace(self, tools: list[BaseTool], workspace) -> list[BaseTool]:
        """Use the same coding definitions on every workspace backend."""
        from agent.modules.tools.coding.adapter import make_coding_tool
        from agent.modules.workspaces import normalize_workspace_ref
        from agent.shared.config.constants import DEFAULT_WORKSPACE_ROOT
        normalize_workspace_ref(workspace, default_locator=DEFAULT_WORKSPACE_ROOT)
        result = list(tools)
        control_tools = {"ask_user", "request_plan_review", "plan_mode_respond", "write_todos"}
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

        ``override_tool_names`` narrows the agent policy for this run. An
        empty iterable disables every tool.
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
        descriptors = service.get_descriptors()
        if not self._include_mcp:
            descriptors = [d for d in descriptors if d.source is not ToolSource.MCP]
        filtered = policy.filter(descriptors)
        if policy.allowed_tool_names is not None:
            known = {d.name for d in descriptors}
            unknown = sorted(set(policy.allowed_tool_names) - known)
            if unknown:
                logger.warning(
                    "Agent '%s' requests unknown tools %s; they resolve to no tool.",
                    policy.agent_name,
                    unknown,
                )
        return filtered

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
            else ToolPolicy(agent_name=agent_name or "default", allowed_tool_names=frozenset())
        )
        if override_tool_names is not None:
            from agent.modules.tools.coding.names import canonical_tool_names
            override_set = frozenset(canonical_tool_names(list(override_tool_names)))
            if policy.allowed_tool_names is not None:
                override_set &= policy.allowed_tool_names
            policy = replace(
                policy,
                allowed_tool_names=override_set,
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
