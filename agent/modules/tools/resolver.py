"""Per-agent tool resolver.

Combines the unified registry with a :class:`ToolPolicy` to return the exact
set of tools an agent may use at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.tools import BaseTool

from agent.modules.tools.config import materialize_tool
from agent.modules.tools.domain import AUXILIARY_MACHINES_TAG, ToolDescriptor, ToolSource
from agent.modules.tools.policy import ACP_UNAVAILABLE_TOOL_NAMES, COMPUTER_TOOL_TAG, DISCOVERY_TOOL_NAMES, ToolPolicy, tools_for_run
from agent.modules.tools.registry_service import (
    ensure_mcp_loaded,
    get_registry_service,
)

logger = logging.getLogger(__name__)

EXPLICIT_CONTROL_TOOL_NAMES = frozenset({
    "ask_user", "browser_handoff", "plan_mode_respond", "request_plan_review", "write_todos",
})


def _tool_tags(tool: BaseTool) -> frozenset[str]:
    from agent.modules.tools.decorators import META_ATTR

    meta = getattr(tool, META_ATTR, None)
    return frozenset(getattr(meta, "tags", ()) or ())


def _has_desktop(workspace) -> bool:
    from agent.modules.workspaces import has_workspace_computer, normalize_workspace_ref
    from agent.shared.config.constants import DEFAULT_WORKSPACE_ROOT

    ref = normalize_workspace_ref(workspace, default_locator=DEFAULT_WORKSPACE_ROOT)
    return has_workspace_computer(ref.backend)


@dataclass
class AgentToolCatalog:
    """Eligible registry descriptors for this agent and run, before workspace filtering."""

    descriptors: dict[str, ToolDescriptor]
    preloaded: frozenset[str]
    tool_configs: dict[str, dict[str, Any]] | None
    auxiliary_machines: bool

    def materialize(self, name: str) -> BaseTool:
        descriptor = self.descriptors[name]
        tool = materialize_tool(descriptor, self.tool_configs)
        if self.auxiliary_machines:
            from agent.modules.tools.builtin.coding_tools import targeted_tool

            tool = targeted_tool(name) or tool
        tool = tool.model_copy()
        tool.metadata = {**(tool.metadata or {}), "permission_risk": descriptor.risk,
                         "permission_self_authorizing": descriptor.self_authorizing,
                         "permission_may_interrupt": descriptor.may_interrupt}
        object.__setattr__(tool, "permission_prepare", descriptor.prepare_permissions)
        return tool

    def deferred_for_run(self, workspace, config) -> dict[str, ToolDescriptor]:
        if not DISCOVERY_TOOL_NAMES.issubset(self.preloaded):
            return {}
        deferred = [
            descriptor for name, descriptor in self.descriptors.items()
            if name not in self.preloaded and name not in EXPLICIT_CONTROL_TOOL_NAMES
        ]
        if not self.auxiliary_machines and not _has_desktop(workspace):
            deferred = [descriptor for descriptor in deferred if not descriptor.has_tag(COMPUTER_TOOL_TAG)]
        return {descriptor.name: descriptor for descriptor in tools_for_run(deferred, config)}


class ToolResolver:
    """Resolve tools for a given agent according to its policy."""

    def __init__(self, *, include_mcp: bool = True) -> None:
        self._include_mcp = include_mcp

    def for_workspace(self, tools: list[BaseTool], workspace) -> list[BaseTool]:
        """Use the same coding definitions on every workspace backend.

        Desktop tools are dropped when neither the workspace nor an auxiliary
        machine the agent can reach has a desktop.
        """
        from agent.modules.tools.coding.adapter import make_coding_tool
        result = list(tools)
        auxiliary_machines = any(AUXILIARY_MACHINES_TAG in _tool_tags(tool) for tool in result)
        catalog = discovery_catalog(result)
        auxiliary_machines = auxiliary_machines or bool(catalog and catalog.auxiliary_machines)
        if not auxiliary_machines and not _has_desktop(workspace):
            result = [tool for tool in result if COMPUTER_TOOL_TAG not in _tool_tags(tool)]
        control_tools = {"ask_user", "request_plan_review", "plan_mode_respond", "write_todos"}
        if result and not any(tool.name == "read" for tool in result) and any(tool.name not in control_tools for tool in result):
            result.append(make_coding_tool("read", retained_only=True, targeted=auxiliary_machines))
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
        if config is not None and policy.allow_delegate_task:
            from agent.modules.agents import get_catalog_service

            policy = replace(
                policy,
                allow_delegate_task=bool(get_catalog_service().get_callable_agents(agent_name)),
            )
        selected = policy.allowed_tool_names
        if override_tool_names is not None:
            from agent.modules.tools.coding.names import canonical_tool_names
            override_set = frozenset(canonical_tool_names(list(override_tool_names)))
            selected = override_set if selected is None else selected & override_set
        discovery = selected is not None and DISCOVERY_TOOL_NAMES.issubset(selected)
        policy = replace(
            policy, allowed_tool_names=None if discovery and override_tool_names is None else selected,
        )
        if getattr(config, "graph_type", "") == "acp_agent":
            policy = replace(policy, deny_tool_names=policy.deny_tool_names | ACP_UNAVAILABLE_TOOL_NAMES)
        agent_tool_configs = (
            getattr(config, "tool_configs", None)
            if config is not None
            else None
        )
        descriptors = self.resolve_for_policy(policy)
        if not discovery:
            descriptors = [d for d in descriptors if d.name not in DISCOVERY_TOOL_NAMES]
        catalog = AgentToolCatalog(
            {d.name: d for d in descriptors},
            frozenset(d.name for d in descriptors if selected is None or d.name in selected),
            agent_tool_configs, any(d.has_tag(AUXILIARY_MACHINES_TAG) for d in descriptors),
        )
        tools = [catalog.materialize(name) for name in catalog.descriptors if name in catalog.preloaded]
        if discovery:
            for tool in tools:
                if tool.name == "search_tools":
                    object.__setattr__(tool, "discovery_catalog", catalog)
        return tools

    @staticmethod
    def _load_agent_config(agent_name: str):
        # local import to avoid a circular dependency between agents and tools
        from agent.modules.agents import get_catalog_service

        catalog = get_catalog_service()
        return catalog.get_agent(agent_name) if agent_name else None


def discovery_catalog(tools: Iterable[BaseTool]) -> AgentToolCatalog | None:
    return next((getattr(tool, "discovery_catalog", None) for tool in tools
                 if getattr(tool, "name", None) == "search_tools"), None)


__all__ = ["AgentToolCatalog", "ToolResolver", "discovery_catalog"]
