from types import SimpleNamespace

import pytest

from agent.modules.mcp import MCPServerConfig, MCPTransport
from agent.modules.mcp.install_repository import McpInstallRepository
from agent.modules.mcp.migrations import migrate_mcp_tables
from agent.shared.infrastructure.db import load_orm_models


@pytest.mark.parametrize("servers", ["github, slack", "[github, slack]"])
def test_migration_preserves_legacy_agent_server_bindings(tmp_path, monkeypatch, servers):
    card_path = tmp_path / "worker.md"
    card_path.write_text(
        f"---\nname: worker\nmcp_servers: {servers}\n---\nPrompt\n",
        encoding="utf-8",
    )
    database_url = f"sqlite:///{(tmp_path / 'mcp.sqlite').as_posix()}"
    configs = {
        name: MCPServerConfig(name=name, transport=MCPTransport.STDIO, command="probe")
        for name in ("github", "slack")
    }
    key_path = tmp_path / "mcp.key"
    monkeypatch.setattr(
        "agent.modules.mcp.migrations.McpInstallRepository",
        lambda url: McpInstallRepository(url, key_path=key_path),
    )
    monkeypatch.setattr(
        "agent.modules.mcp.repository.ConfigMcpServerRepository._load_legacy",
        lambda self: configs,
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: SimpleNamespace(list_agent_cards=lambda: [
            SimpleNamespace(name="worker", valid=True, path=str(card_path)),
        ]),
    )
    load_orm_models()

    migrate_mcp_tables(database_url)

    repository = McpInstallRepository(database_url, key_path=key_path)
    try:
        assert sorted(repository.list_agent_server_names("worker")) == ["github", "slack"]
    finally:
        repository.close()
