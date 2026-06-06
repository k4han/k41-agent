"""Tests for the MCP module: repository, catalog, service helpers."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest import MonkeyPatch

from agent.modules.mcp import (
    POPULAR_MCP_SERVERS,
    MCPServerConfig,
    MCPTransport,
    parse_mcp_server_key,
)
from agent.modules.mcp.client import (
    build_connection_payload,
    format_exception_chain,
    prefixed_tool_name,
    tools_to_info,
)
from agent.modules.mcp.repository import ConfigMcpServerRepository
from agent.shared.config import ConfigService, SettingsSource, SettingsValue
from agent.shared.config.constants import is_database_runtime_key
from agent.shared.config.default_source import DefaultConfigSource
from agent.shared.infrastructure.config_file import flatten_config_mapping


def _set_config_path(monkeypatch: MonkeyPatch, path: Path) -> None:
    import agent.shared.config.service as service_module
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flat = flatten_config_mapping(raw)
    source = _RuntimeSource(
        {
            key: SettingsValue(key=key, value=value, source=SettingsSource.DATABASE)
            for key, value in flat.items()
            if is_database_runtime_key(key)
        }
    )
    service = ConfigService(sources=[DefaultConfigSource(), source])
    monkeypatch.setattr(service_module, "_config_service", service)
    monkeypatch.setattr(service_module, "_config_sources", service._sources)


class _RuntimeSource:
    def __init__(self, entries: dict[str, SettingsValue]) -> None:
        self._entries = entries
        self._priority = 200

    def get(self, key: str) -> object | None:
        value = self._entries.get(key)
        return value.value if value else None

    def get_all(self) -> dict[str, object]:
        return {key: value.value for key, value in self._entries.items()}

    def get_settings_value(self, key: str) -> SettingsValue | None:
        return self._entries.get(key)

    def get_all_settings_values(self, keys: set[str] | None = None) -> dict[str, SettingsValue]:
        if keys is None:
            return dict(self._entries)
        return {key: value for key, value in self._entries.items() if key in keys}

    def reload(self) -> None:
        pass

    @property
    def priority(self) -> int:
        return self._priority


def test_parse_mcp_server_key_handles_nested_paths() -> None:
    assert parse_mcp_server_key("mcp.servers.foo.transport") == ("foo", "transport")
    assert parse_mcp_server_key("mcp.servers.foo.env.GITHUB_TOKEN") == (
        "foo",
        "env.GITHUB_TOKEN",
    )
    assert parse_mcp_server_key("mcp.servers.my-srv.headers.Authorization") == (
        "my-srv",
        "headers.Authorization",
    )
    assert parse_mcp_server_key("llm.providers.foo.api_key") is None


def test_popular_catalog_has_required_fields() -> None:
    assert len(POPULAR_MCP_SERVERS) >= 10
    seen_ids: set[str] = set()
    for entry in POPULAR_MCP_SERVERS:
        assert entry.id and entry.id not in seen_ids
        seen_ids.add(entry.id)
        assert entry.name
        assert entry.description
        if entry.transport == MCPTransport.STDIO:
            assert entry.command


def test_prefixed_tool_name_sanitizes_server_token() -> None:
    assert prefixed_tool_name("my-server", "read_file") == "mcp__my-server__read_file"
    assert prefixed_tool_name("weird server!", "x") == "mcp__weird_server___x"


def test_build_connection_payload_stdio_and_http() -> None:
    stdio = MCPServerConfig(
        name="fs",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "/root"),
        env={"FOO": "bar"},
    )
    payload = build_connection_payload(stdio)
    assert payload == {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/root"],
        "env": {"FOO": "bar"},
    }

    http = MCPServerConfig(
        name="remote",
        transport=MCPTransport.HTTP,
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer x"},
    )
    payload = build_connection_payload(http)
    assert payload == {
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer x"},
    }


def test_build_connection_payload_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        build_connection_payload(
            MCPServerConfig(name="x", transport=MCPTransport.STDIO)
        )
    with pytest.raises(ValueError):
        build_connection_payload(
            MCPServerConfig(name="x", transport=MCPTransport.HTTP)
        )


def test_tools_to_info_strips_server_prefix() -> None:
    class _StubTool:
        def __init__(self, name: str, description: str = "") -> None:
            self.name = name
            self.description = description

    tools = [
        _StubTool("mcp__foo__list", "List things"),
        _StubTool("mcp__foo__write", ""),
    ]
    infos = tools_to_info(tools, "foo")
    assert [info.name for info in infos] == ["list", "write"]
    assert [info.prefixed_name for info in infos] == [
        "mcp__foo__list",
        "mcp__foo__write",
    ]


def test_repository_reads_mcp_servers_from_runtime_config(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            mcp:
              servers:
                filesystem:
                  transport: stdio
                  command: npx
                  args:
                    - "-y"
                    - "@modelcontextprotocol/server-filesystem"
                    - "/tmp/x"
                  enabled: true
                remote:
                  transport: streamable_http
                  url: https://example.com/mcp
                  headers:
                    Authorization: "Bearer xxx"
                  enabled: false
            """
        ).strip(),
        encoding="utf-8",
    )
    _set_config_path(monkeypatch, config_path)
    monkeypatch.setattr(
        "agent.modules.mcp.repository.ConfigMcpServerRepository._load_db",
        lambda self: {},
    )

    repo = ConfigMcpServerRepository()
    servers = {server.name.lower(): server for server in repo.list_servers()}
    assert set(servers) == {"filesystem", "remote"}

    fs = servers["filesystem"]
    assert fs.transport == MCPTransport.STDIO
    assert fs.command == "npx"
    assert fs.args == (
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/tmp/x",
    )
    assert fs.enabled is True

    remote = servers["remote"]
    assert remote.transport == MCPTransport.HTTP
    assert remote.url == "https://example.com/mcp"
    assert remote.headers == {"Authorization": "Bearer xxx"}
    assert remote.enabled is False


def test_repository_returns_empty_when_no_servers(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm: {}\n", encoding="utf-8")
    _set_config_path(monkeypatch, config_path)
    monkeypatch.setattr(
        "agent.modules.mcp.repository.ConfigMcpServerRepository._load_db",
        lambda self: {},
    )

    repo = ConfigMcpServerRepository()
    assert repo.list_servers() == []


def test_runtime_key_allows_mcp_pattern() -> None:
    from agent.shared.config.constants import is_runtime_key

    assert is_runtime_key("mcp.servers.foo.transport")
    assert is_runtime_key("mcp.servers.foo.env.GITHUB_TOKEN")
    assert is_runtime_key("mcp.servers.foo-bar.headers.Authorization")
    assert not is_runtime_key("mcp.servers.foo.unknown_field")


def test_format_exception_chain_unwraps_exception_group() -> None:
    inner = FileNotFoundError("missing-binary")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    message = format_exception_chain(group)
    assert "FileNotFoundError" in message
    assert "missing-binary" in message
    assert "TaskGroup" not in message


def test_format_exception_chain_handles_nested_groups() -> None:
    leaf_a = RuntimeError("boom-a")
    leaf_b = ValueError("boom-b")
    nested = ExceptionGroup("inner", [leaf_a])
    group = ExceptionGroup("outer", [nested, leaf_b])
    message = format_exception_chain(group)
    assert "RuntimeError: boom-a" in message
    assert "ValueError: boom-b" in message


@pytest.mark.asyncio
async def test_service_test_connection_catches_exceptions() -> None:
    from agent.modules.mcp.service import MCPService

    repo = ConfigMcpServerRepository()
    service = MCPService(repository=repo)
    config = MCPServerConfig(
        name="bad",
        transport=MCPTransport.STDIO,
        command="this-command-does-not-exist-xyz",
        args=(),
    )
    with patch(
        "agent.modules.mcp.service.fetch_tools_for_config",
        side_effect=RuntimeError("boom"),
    ):
        result = await service.test_connection(config)
    assert result.ok is False
    assert "boom" in result.error
