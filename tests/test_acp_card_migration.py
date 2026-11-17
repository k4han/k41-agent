"""Legacy ACP cards become independent without losing their effective configuration."""

import json

import pytest
import yaml
from sqlalchemy import create_engine

from agent.modules.agents.models import AgentConfig
from agent.modules.agents.parser import parse_agent_file, serialize_agent_config
from agent.modules.mcp import MCPServerConfig, MCPTransport
from agent.modules.mcp.db_models import AgentMCPInstall, MCPCredential, MCPServerInstall
from agent.modules.mcp.install_repository import McpInstallRepository
from agent.shared.infrastructure.db import Base
from scripts import migrate_acp_cards as migration


@pytest.fixture
def legacy_cards(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    source = AgentConfig(name="source", graph_type="react_agent", tools=["read", "ask_user"],
        permission_mode="auto", tool_permissions=[{"action": "shell.execute", "effect": "deny"}],
        tool_configs={"read": {"limit": 3}}, system_prompt="Source instructions.")
    (agents / "source.md").write_text(serialize_agent_config(source), encoding="utf-8")
    for name in ("first", "second"):
        data = {"name": name, "graph_type": "acp_agent", "tools": ["write"], "permission_mode": "full_access",
            "acp": {"agent": "antigravity-cli", "base_agent": "source", "config_options": {"model": "custom"}}}
        (agents / f"{name}.md").write_text("---\n" + yaml.safe_dump(data) + "---\nIgnored prompt.\n", encoding="utf-8")
    database_url = f"sqlite:///{(tmp_path / 'mcp.db').as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine, tables=[MCPCredential.__table__, MCPServerInstall.__table__, AgentMCPInstall.__table__])
    engine.dispose()
    mcp = McpInstallRepository(database_url, key_path=tmp_path / "key")
    for name in ("shared", "disabled", "old"):
        mcp.seed_server_install(server_name=name, config=MCPServerConfig(name=name, transport=MCPTransport.STDIO, command="unused"))
    mcp.bind_agent_server(agent_name="source", server_name="shared")
    mcp.bind_agent_server(agent_name="source", server_name="disabled", enabled=False)
    mcp.bind_agent_server(agent_name="first", server_name="old")
    yield agents, source, mcp
    mcp.close()


def test_migration_preserves_effective_settings_and_mcp_and_is_repeatable(legacy_cards):
    agents, source, mcp = legacy_cards
    original = (agents / "first.md").read_bytes()
    original_source = (agents / "source.md").read_bytes()
    backup = migration.migrate_cards(agents, ["first", "second"], mcp)
    assert (backup / "first.md").read_bytes() == original
    assert json.loads((backup / "bindings.json").read_text())["first"] == [{"server_name": "old", "enabled": True}]
    for name in ("first", "second"):
        card = parse_agent_file(agents / f"{name}.md")
        assert card.system_prompt == source.system_prompt
        assert card.tools == ["read"] and card.tool_configs == source.tool_configs
        assert card.permission_mode == source.permission_mode and card.tool_permissions == source.tool_permissions
        assert card.acp.agent == "antigravity-cli" and card.acp.config_options == {"model": "custom"}
        assert "base_agent" not in (agents / f"{name}.md").read_text()
        assert migration._bindings(mcp, name) == migration._bindings(mcp, "source")
        assert mcp.list_agent_server_names(name) == ["shared"]
    assert (agents / "source.md").read_bytes() == original_source
    assert migration.migrate_cards(agents, ["first", "second"], mcp) is None


def test_migration_resolves_legacy_card_name_independently_of_filename(legacy_cards):
    agents, source, mcp = legacy_cards
    path = agents / "custom-title.md"
    (agents / "first.md").rename(path)
    original = path.read_bytes()

    backup = migration.migrate_cards(agents, ["first"], mcp)

    card = parse_agent_file(path)
    assert card.name == "first" and card.system_prompt == source.system_prompt
    assert card.tools == ["read"] and card.tool_configs == source.tool_configs
    assert card.permission_mode == source.permission_mode and card.tool_permissions == source.tool_permissions
    assert migration._bindings(mcp, "first") == migration._bindings(mcp, "source")
    assert (backup / "first.md").read_bytes() == original
    assert not (agents / "first.md").exists()
    assert migration.migrate_cards(agents, ["first"], mcp) is None


def test_duplicate_card_names_fail_before_writing_anything(legacy_cards):
    agents, _, mcp = legacy_cards
    original = (agents / "first.md").read_bytes()
    (agents / "duplicate.md").write_bytes(original)
    bindings = migration._bindings(mcp, "first")

    with pytest.raises(ValueError, match="Multiple user cards"):
        migration.migrate_cards(agents, ["first"], mcp)
    assert (agents / "first.md").read_bytes() == original
    assert (agents / "duplicate.md").read_bytes() == original
    assert migration._bindings(mcp, "first") == bindings
    assert not (agents / ".acp-card-backups").exists()


@pytest.mark.parametrize("failure", ["file", "binding"])
def test_migration_rolls_back_every_card_and_binding_on_failure(legacy_cards, monkeypatch, failure):
    agents, _, mcp = legacy_cards
    originals = {name: (agents / f"{name}.md").read_bytes() for name in ("first", "second")}
    bindings = {name: migration._bindings(mcp, name) for name in originals}
    if failure == "file":
        write = migration._write_card

        def fail_once(path, content):
            if path.name == "second.md":
                monkeypatch.setattr(migration, "_write_card", write)
                raise OSError("write failed")
            write(path, content)

        monkeypatch.setattr(migration, "_write_card", fail_once)
    else:
        bind = mcp.bind_agent_server

        def fail_once(**kwargs):
            if kwargs["agent_name"] == "second":
                monkeypatch.setattr(mcp, "bind_agent_server", bind)
                raise OSError("binding failed")
            return bind(**kwargs)

        monkeypatch.setattr(mcp, "bind_agent_server", fail_once)
    with pytest.raises(OSError, match="failed"):
        migration.migrate_cards(agents, list(originals), mcp)
    for name, original in originals.items():
        assert (agents / f"{name}.md").read_bytes() == original
        assert migration._bindings(mcp, name) == bindings[name]


def test_missing_base_fails_before_writing_anything(legacy_cards):
    agents, _, mcp = legacy_cards
    (agents / "source.md").unlink()
    original = (agents / "first.md").read_bytes()
    with pytest.raises(ValueError, match="unavailable base"):
        migration.migrate_cards(agents, ["first"], mcp)
    assert (agents / "first.md").read_bytes() == original
    assert not (agents / ".acp-card-backups").exists()
