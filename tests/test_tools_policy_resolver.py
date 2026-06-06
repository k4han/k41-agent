"""Tests for ToolPolicy, ToolResolver, and McpToolSource."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from agent.modules.tools import (
    ToolCapability,
    ToolCategory,
    ToolDescriptor,
    ToolPolicy,
    ToolResolver,
    ToolSource,
    resolve_tools_for_agent,
)
from agent.modules.tools.sources.mcp import (
    MCP_TOOL_PREFIX,
    McpToolSource,
    _extract_server_name,
)


@tool
def _t_call_agent_stub(task: str) -> str:
    """stub."""
    return task


@tool
def _t_read_stub(file_path: str) -> str:
    """stub."""
    return file_path


def _builtin_descriptor(name: str, tool_obj) -> ToolDescriptor:
    return ToolDescriptor(
        id=f"builtin.utility.{name}",
        name=name,
        description="desc",
        source=ToolSource.BUILTIN,
        category=ToolCategory.UTILITY,
        tool=tool_obj,
        capabilities=frozenset(),
    )


def _mcp_descriptor(name: str, tool_obj) -> ToolDescriptor:
    return ToolDescriptor(
        id=f"mcp.x.{name}",
        name=name,
        description="desc",
        source=ToolSource.MCP,
        category=ToolCategory.UNKNOWN,
        tool=tool_obj,
        capabilities=frozenset({ToolCapability.NETWORK}),
    )


class TestExtractServerName:
    def test_standard_prefix(self) -> None:
        assert _extract_server_name("mcp__github__create_issue") == "github"

    def test_missing_prefix(self) -> None:
        assert _extract_server_name("plain_tool") == "unknown"

    def test_single_segment(self) -> None:
        assert _extract_server_name("mcp__solo") == "unknown"


class TestToolPolicy:
    def test_allow_all_default(self) -> None:
        policy = ToolPolicy.allow_all()
        d_read = _builtin_descriptor("read_file", _t_read_stub)
        d_call = _builtin_descriptor("call_agent", _t_call_agent_stub)
        assert policy.is_allowed(d_read)
        assert policy.is_allowed(d_call)

    def test_explicit_allow_list_filters_builtins(self) -> None:
        policy = ToolPolicy(
            agent_name="x",
            allowed_tool_names=frozenset({"read_file"}),
        )
        assert policy.is_allowed(_builtin_descriptor("read_file", _t_read_stub))
        assert not policy.is_allowed(_builtin_descriptor("write_file", _t_read_stub))

    def test_disallow_call_agent_when_sub_agents_none(self) -> None:
        config = SimpleNamespace(
            name="leaf",
            tools=[],
            mcp_servers=None,
            sub_agents=None,
        )
        policy = ToolPolicy.from_agent_config(config)
        assert not policy.is_allowed(_builtin_descriptor("call_agent", _t_call_agent_stub))
        assert policy.is_allowed(_builtin_descriptor("read_file", _t_read_stub))

    def test_allow_call_agent_when_sub_agents_list(self) -> None:
        config = SimpleNamespace(
            name="parent",
            tools=[],
            mcp_servers=None,
            sub_agents=["child"],
        )
        policy = ToolPolicy.from_agent_config(config)
        assert policy.is_allowed(_builtin_descriptor("call_agent", _t_call_agent_stub))

    def test_default_agent_auto_includes_mcp_when_empty_list(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "agent.modules.mcp.list_agent_mcp_server_names",
            lambda name: [],
        )
        config = SimpleNamespace(
            name="default",
            tools=["echo"],
            mcp_servers=[],
            sub_agents=None,
        )
        policy = ToolPolicy.from_agent_config(config)
        d_gh = _mcp_descriptor("mcp__github__list_repos", _t_read_stub)
        assert policy.is_allowed(d_gh)
        assert policy.auto_include_all_mcp is True

    def test_default_agent_auto_includes_mcp_when_mcp_servers_missing(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "agent.modules.mcp.list_agent_mcp_server_names",
            lambda name: [],
        )
        config = SimpleNamespace(
            name="default",
            tools=["echo"],
            mcp_servers=None,
            sub_agents=None,
        )
        policy = ToolPolicy.from_agent_config(config)
        d_gh = _mcp_descriptor("mcp__github__list_repos", _t_read_stub)
        assert policy.is_allowed(d_gh)
        assert policy.auto_include_all_mcp is True

    def test_mcp_server_allow_list(self) -> None:
        policy = ToolPolicy(
            agent_name="x",
            allowed_mcp_servers=frozenset({"github"}),
        )
        d_gh = _mcp_descriptor("mcp__github__list_repos", _t_read_stub)
        d_other = _mcp_descriptor("mcp__slack__post", _t_read_stub)
        assert policy.is_allowed(d_gh)
        assert not policy.is_allowed(d_other)

    def test_deny_overrides_allow(self) -> None:
        policy = ToolPolicy(
            agent_name="x",
            deny_tool_names=frozenset({"read_file"}),
        )
        assert not policy.is_allowed(_builtin_descriptor("read_file", _t_read_stub))

    def test_filter_returns_subset(self) -> None:
        policy = ToolPolicy(
            agent_name="x",
            allowed_tool_names=frozenset({"read_file"}),
        )
        descriptors = [
            _builtin_descriptor("read_file", _t_read_stub),
            _builtin_descriptor("write_file", _t_read_stub),
        ]
        filtered = policy.filter(descriptors)
        assert [d.name for d in filtered] == ["read_file"]


class TestToolResolver:
    def test_resolve_for_unknown_agent_returns_default_allow_all(self) -> None:
        tools = resolve_tools_for_agent("__no_such_agent__")
        names = {t.name for t in tools}
        # default policy = allow_all, so all builtin tools come back
        assert "read_file" in names
        assert "echo" in names

    def test_resolve_for_default_agent(self) -> None:
        tools = resolve_tools_for_agent("default")
        names = {t.name for t in tools}
        # default agent in this project allows the full builtin set
        assert "read_file" in names

    def test_resolver_uses_policy_for_filtering(self, monkeypatch) -> None:
        fake_config = SimpleNamespace(
            name="restricted",
            tools=["echo"],
            mcp_servers=None,
            sub_agents=None,
        )

        class _FakeCatalog:
            def get_agent(self, name: str):
                if name == "restricted":
                    return fake_config
                return None

        from agent.modules.tools import resolver as resolver_mod

        monkeypatch.setattr(
            "agent.modules.agents.get_catalog_service",
            lambda: _FakeCatalog(),
        )

        tools = ToolResolver().resolve_for_agent("restricted")
        names = {t.name for t in tools}
        assert names == {"echo"}


class TestMcpToolSource:
    @pytest.mark.asyncio
    async def test_load_returns_empty_when_no_servers(self, monkeypatch) -> None:
        async def _fake():
            return []

        monkeypatch.setattr("agent.modules.mcp.get_all_mcp_tools", _fake)
        descriptors = await McpToolSource().load()
        assert descriptors == []

    @pytest.mark.asyncio
    async def test_load_wraps_tools_with_mcp_source(self, monkeypatch) -> None:
        @tool
        def mcp__github__list(repo: str) -> str:
            """list."""
            return repo

        async def _fake():
            return [mcp__github__list]

        monkeypatch.setattr("agent.modules.mcp.get_all_mcp_tools", _fake)
        descriptors = await McpToolSource().load()
        assert len(descriptors) == 1
        d = descriptors[0]
        assert d.source is ToolSource.MCP
        assert d.category is ToolCategory.UNKNOWN
        assert d.name == "mcp__github__list"
        assert d.id.startswith(f"{ToolSource.MCP.value}.github.")
        assert "github" in d.tags
        assert "mcp" in d.tags

    @pytest.mark.asyncio
    async def test_load_skips_non_basetool(self, monkeypatch) -> None:
        async def _fake():
            return ["not-a-tool", 123]

        monkeypatch.setattr("agent.modules.mcp.get_all_mcp_tools", _fake)
        descriptors = await McpToolSource().load()
        assert descriptors == []

    def test_prefix_constant(self) -> None:
        assert MCP_TOOL_PREFIX == "mcp__"
