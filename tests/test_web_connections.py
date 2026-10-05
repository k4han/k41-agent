"""Named connection migration, live resolution, and dashboard contracts."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agent.shared.config import ConfigService
from agent.shared.config.database_source import DatabaseConfigSource
from agent.shared.config.default_source import DefaultConfigSource
from agent.shared.config.web_connections import (
    MIGRATION_KEY, connection_entries, migration_updates, resolve_web_field,
)
from agent.shared.infrastructure.db.runtime_settings import RuntimeSetting
from test_dashboard_settings import _db_config_service, make_dashboard_client  # noqa: F401


@pytest.fixture()
def database_source(tmp_path):
    source = DatabaseConfigSource(f"sqlite:///{tmp_path / 'connections.db'}", key_path=tmp_path / "config.key")
    RuntimeSetting.__table__.create(source._engine)
    yield source
    source.close()


def test_migration_shares_equal_credentials_and_preserves_original_rows(database_source):
    source = database_source
    source.update_settings({"tools.web_search.tavily_api_key": "shared-secret"})
    source.migrate_web_connections()
    flat = source.get_all()
    assert flat[MIGRATION_KEY] == 1
    assert len(connection_entries(flat)) == 1
    assert flat["tools.web_search.tavily_connection"] == "__default__"
    assert flat["tools.web_fetch.tavily_connection"] == "__default__"
    assert flat["tools.web_search.tavily_api_key"] == "shared-secret"
    assert source.migrate_web_connections() == set()
    with source._session_maker() as session:
        row = session.execute(select(RuntimeSetting).where(RuntimeSetting.key.like("web.connections.%.api_key"))).scalar_one()
        assert row.encrypted is True
        assert "shared-secret" not in row.value_json


def test_migration_preserves_distinct_effective_profiles():
    flat = {"tools.web_search.firecrawl_api_key": "search-key",
            "tools.web_fetch.firecrawl_api_key": "fetch-key",
            "tools.web_fetch.firecrawl_base_url": "https://crawl.example/v2"}
    updates = migration_updates(flat)
    service, _ = _db_config_service("{}")
    service._sources[-1].update_settings({**flat, **updates})
    assert resolve_web_field(service, "web_search", "firecrawl", "api_key", {})[0] == "search-key"
    assert resolve_web_field(service, "web_fetch", "firecrawl", "api_key", {})[0] == "fetch-key"
    assert resolve_web_field(service, "web_search", "firecrawl", "base_url", {})[0] == "https://crawl.example/v2"
    assert len(connection_entries(updates)) == 2
    assert updates["web.defaults.firecrawl"] == "firecrawl-legacy-web-search"


def test_migration_does_not_copy_environment_or_overwrite_new_references(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "environment-only")
    assert migration_updates({}) == {MIGRATION_KEY: 1}
    flat = {"web.connections.saved.type": "tavily", "web.connections.saved.api_key": "new-key",
            "web.defaults.tavily": "saved", "tools.web_search.tavily_connection": "saved",
            "tools.web_search.tavily_api_key": "old-key"}
    updates = migration_updates(flat)
    assert "web.defaults.tavily" not in updates
    assert "tools.web_search.tavily_connection" not in updates
    assert "web.connections.saved.api_key" not in updates
    assert updates["tools.web_fetch.tavily_connection"] != "__default__"


def test_migration_rolls_back_every_write_on_failure(database_source, monkeypatch):
    source = database_source
    source.update_settings({"tools.web_search.tavily_api_key": "old-key"})
    original = source._encode_value

    def fail_at_marker(value, *, encrypted):
        if value == 1:
            raise RuntimeError("simulated migration failure")
        return original(value, encrypted=encrypted)

    monkeypatch.setattr(source, "_encode_value", fail_at_marker)
    with pytest.raises(RuntimeError, match="simulated"):
        source.migrate_web_connections()
    source.reload()
    assert source.get_all() == {"tools.web_search.tavily_api_key": "old-key"}


def test_live_connections_override_environment_and_ignore_archived_credentials(monkeypatch):
    service, source = _db_config_service("{}")
    source.update_settings({MIGRATION_KEY: 1, "web.connections.team.type": "tavily",
                            "web.connections.team.api_key": "initial-key", "web.defaults.tavily": "team",
                            "tools.web_search.tavily_api_key": "archived-key"})
    monkeypatch.setenv("TAVILY_API_KEY", "env-key")
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {}) == ("initial-key", "connection:team")
    source.update_setting("web.connections.team.api_key", "changed-key")
    assert resolve_web_field(service, "web_fetch", "tavily", "api_key", {})[0] == "changed-key"
    assert resolve_web_field(service, "web_fetch", "tavily", "api_key", {"tavily_api_key": "agent-key"})[0] == "agent-key"
    source.update_setting("web.connections.team.api_key", "")
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {}) == ("env-key", "environment:TAVILY_API_KEY")
    with pytest.raises(ValueError, match="Invalid tavily"):
        resolve_web_field(service, "web_fetch", "tavily", "api_key", {"tavily_connection": "missing"})


def test_agent_can_use_shared_default_instead_of_tool_connection():
    service, source = _db_config_service("{}")
    source.update_settings({"web.connections.shared.type": "tavily", "web.connections.shared.api_key": "shared-key",
                            "web.connections.private.type": "tavily", "web.connections.private.api_key": "private-key",
                            "web.defaults.tavily": "shared", "tools.web_search.tavily_connection": "private"})
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {})[0] == "private-key"
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {"tavily_connection": "__default__"})[0] == "shared-key"


def test_api_crud_masks_keys_and_checks_references(make_dashboard_client, monkeypatch):
    service, source = _db_config_service("{}")
    client = make_dashboard_client(service)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(list_agent_cards=lambda: []))
    api = "/dashboard-api/web-connections"
    assert client.post(api, json={"name": "Team", "type": "tavily", "api_key": "private-secret"}).status_code == 200
    assert client.post(api, json={"name": "team", "type": "tavily"}).status_code == 409
    assert client.post(api, json={"name": "__default__", "type": "tavily"}).status_code == 400
    assert client.post(api, json={"name": "bad", "type": "tavily", "base_url": "https://example.com"}).status_code == 400
    assert client.post(api, json={"name": "bad", "type": "firecrawl", "base_url": "invalid"}).status_code == 400
    for path in (api, "/settings", "/settings/sources", "/dashboard-api/tools", "/dashboard-api/agents/tools"):
        response = client.get(path)
        assert response.status_code == 200
        assert "private-secret" not in response.text
    entry = client.get(api).json()["connections"][0]
    assert entry["has_api_key"] is True and entry["configured"] is True
    assert client.put(f"{api}/Team", json={}).status_code == 200
    assert source.get("web.connections.Team.api_key") == "private-secret"
    assert client.put(f"{api}/defaults/firecrawl", json={"name": "Team"}).status_code == 400
    assert client.put(f"{api}/defaults/tavily", json={"name": "Team"}).status_code == 200
    blocked = client.delete(f"{api}/Team")
    assert blocked.status_code == 409 and "web.defaults.tavily" in blocked.json()["detail"]["references"]
    assert client.put(f"{api}/defaults/tavily", json={"name": None}).status_code == 200
    assert client.put("/settings/tools.web_fetch.tavily_connection", json={"value": "Team"}).status_code == 200
    assert client.delete(f"{api}/Team").status_code == 409
    assert client.put("/settings/tools.web_fetch.firecrawl_connection", json={"value": "Team"}).status_code == 400
    assert client.put("/settings/tools.web_fetch.tavily_connection", json={"value": None}).status_code == 200
    assert client.put(f"{api}/Team", json={"api_key": None}).status_code == 200
    assert source.get("web.connections.Team.api_key") == ""
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(
        list_agent_cards=lambda: [SimpleNamespace(name="research", tool_configs={"web_search": {"tavily_connection": "Team"}})]))
    blocked = client.delete(f"{api}/Team")
    assert "agents.research.web_search.tavily_connection" in blocked.json()["detail"]["references"]
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(list_agent_cards=lambda: []))
    assert client.delete(f"{api}/Team").status_code == 200


def test_legacy_api_forks_shared_connection_and_reset_never_restores_old_key(make_dashboard_client, monkeypatch):
    service, source = _db_config_service("{}")
    source.update_settings({MIGRATION_KEY: 1, "web.connections.shared.type": "tavily",
                            "web.connections.shared.api_key": "shared-key", "web.defaults.tavily": "shared",
                            "tools.web_search.tavily_api_key": "archived-key"})
    client = make_dashboard_client(service)
    response = client.put("/settings", json={"values": {"tools.web_search.tavily_api_key": "separate-key"}})
    assert response.json()["updated"] == ["tools.web_search.tavily_api_key"]
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {})[0] == "separate-key"
    assert resolve_web_field(service, "web_fetch", "tavily", "api_key", {})[0] == "shared-key"
    assert client.put("/settings/tools.web_search.tavily_api_key", json={"value": None}).status_code == 200
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {})[0] == ""
    assert source.get("tools.web_search.tavily_api_key") == "archived-key"
    assert "archived-key" not in client.get("/settings").text


def test_materialized_tool_reads_updated_connection_without_recreation(monkeypatch):
    from agent.modules.tools import ToolSource, find_descriptors
    from agent.modules.tools.config import ToolConfigService
    from agent.modules.tools.builtin.web import web_fetch as fetch_module

    service, source = _db_config_service("{}")
    source.update_settings({MIGRATION_KEY: 1, "web.connections.live.type": "tavily",
                            "web.connections.live.api_key": "first-key", "web.defaults.tavily": "live",
                            "tools.web_fetch.provider": "tavily",
                            "tools.web_fetch.tavily_api_key": "ignored-archive"})
    monkeypatch.setattr("agent.modules.tools.config.get_config_service", lambda: service)
    monkeypatch.setattr("agent.shared.config.get_config_service", lambda: service)
    seen = []

    def fetch(url, *, api_key):
        seen.append(api_key)
        return "page content"

    monkeypatch.setattr(fetch_module, "_tavily_fetch", fetch)
    descriptor = next(item for item in find_descriptors(source=ToolSource.BUILTIN) if item.name == "web_fetch")
    config = ToolConfigService().resolve(descriptor)
    assert "tavily_api_key" not in config
    tool = fetch_module._build_web_fetch_tool(config)
    assert tool.invoke({"url": "https://example.com"}) == "page content"
    source.update_setting("web.connections.live.api_key", "second-key")
    assert tool.invoke({"url": "https://example.com"}) == "page content"
    assert seen == ["first-key", "second-key"]


def test_attach_database_seeds_yaml_before_migration(tmp_path, monkeypatch):
    from agent.shared.config import attach_database_config_source
    from agent.shared.config.yaml_source import YamlConfigSource

    path = tmp_path / "config.yaml"
    path.write_text("tools:\n  web_search:\n    tavily_api_key: yaml-key\n", encoding="utf-8")
    database_url = f"sqlite:///{tmp_path / 'seed.db'}"
    source = DatabaseConfigSource(database_url, key_path=tmp_path / "key")
    RuntimeSetting.__table__.create(source._engine)
    source.close()
    monkeypatch.setattr("agent.shared.config.database_source._default_key_path", lambda: tmp_path / "key")
    service = ConfigService(sources=[DefaultConfigSource(), YamlConfigSource(path=path)])
    attach_database_config_source(database_url, service)
    assert service.get(MIGRATION_KEY) == 1
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {})[0] == "yaml-key"
    service._sources[-1].close()


def test_legacy_edit_forks_again_when_private_connection_becomes_shared(make_dashboard_client, monkeypatch):
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(list_agent_cards=lambda: []))
    service, source = _db_config_service("{}")
    source.update_settings({MIGRATION_KEY: 1})
    client = make_dashboard_client(service)
    path = "/settings/tools.web_search.tavily_api_key"
    assert client.put(path, json={"value": "first-key"}).status_code == 200
    original = source.get("tools.web_search.tavily_connection")
    source.update_setting("web.defaults.tavily", original)
    assert client.put(path, json={"value": "second-key"}).status_code == 200
    assert source.get("tools.web_search.tavily_connection") != original
    assert source.get(f"web.connections.{original}.api_key") == "first-key"
    assert resolve_web_field(service, "web_fetch", "tavily", "api_key", {})[0] == "first-key"
    assert resolve_web_field(service, "web_search", "tavily", "api_key", {})[0] == "second-key"


def test_agent_api_rejects_missing_wrong_type_and_unsupported_connections(make_dashboard_client, monkeypatch):
    service, source = _db_config_service("{}")
    source.update_settings({"web.connections.google-only.type": "google"})
    client = make_dashboard_client(service)
    monkeypatch.setattr("agent.delivery.http.dashboard.routes.agents.get_catalog_service", lambda: SimpleNamespace())
    for values in ({"tavily_connection": "missing"}, {"tavily_connection": "google-only"}, {"brave_connection": "google-only"}):
        response = client.post("/agents/cards", json={"name": "research", "tools": ["web_fetch"],
                                                      "tool_configs": {"web_fetch": values}})
        assert response.status_code == 400


def test_verify_web_connection_routes(make_dashboard_client, monkeypatch):
    import httpx

    service, source = _db_config_service("{}")
    client = make_dashboard_client(service)

    fake_resp = httpx.Response(
        200,
        json={"results": [{"title": "Ping", "url": "https://example.com"}]},
        request=httpx.Request("POST", "https://api.tavily.com/search"),
    )

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return fake_resp

        async def get(self, url, **kwargs):
            return fake_resp

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    # 1. Direct candidate verification
    resp = client.post("/dashboard-api/web-connections/verify", json={"type": "tavily", "api_key": "tav-key"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["latency_ms"] >= 0

    # 2. Saved connection verification
    source.update_settings({"web.connections.tav-saved.type": "tavily", "web.connections.tav-saved.api_key": "saved-k"})
    resp_saved = client.post("/dashboard-api/web-connections/verify", json={"name": "tav-saved"})
    assert resp_saved.status_code == 200
    assert resp_saved.json()["ok"] is True

    # 3. Google CSE requires cse_id
    resp_google_fail = client.post("/dashboard-api/web-connections/verify", json={"type": "google", "api_key": "g-key"})
    assert resp_google_fail.status_code == 200
    assert resp_google_fail.json()["ok"] is False
    assert resp_google_fail.json()["error_code"] == "INVALID_CONFIG"

