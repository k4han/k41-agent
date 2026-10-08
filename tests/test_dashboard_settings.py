"""Tests for dashboard settings endpoints."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi import Request

from agent.shared.config import ConfigService, SettingsSource, SettingsValue
from agent.shared.config.constants import is_database_runtime_key
from agent.shared.config.default_source import DefaultConfigSource
from agent.shared.config.yaml_source import YamlConfigSource
from agent.shared.infrastructure.config_file import flatten_config_mapping


class StubSource:
    def __init__(self, entries: dict[str, SettingsValue], priority: int = 100) -> None:
        self._entries = entries
        self._priority = priority

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


class WritableDbSource(StubSource):
    def __init__(self, entries: dict[str, SettingsValue] | None = None) -> None:
        super().__init__(entries or {}, priority=200)

    def can_update_key(self, key: str) -> bool:
        return is_database_runtime_key(key)

    def update_settings(self, updates: dict[str, object]) -> None:
        for key, value in updates.items():
            if not self.can_update_key(key):
                continue
            self._entries[key] = SettingsValue(
                key=key,
                value=value,
                source=SettingsSource.DATABASE,
            )

    def update_setting(self, key: str, value: object) -> None:
        self.update_settings({key: value})

    def delete_setting_tree(self, key: str) -> bool:
        prefix = f"{key}."
        deleted = False
        for existing_key in list(self._entries):
            if existing_key == key or existing_key.startswith(prefix):
                del self._entries[existing_key]
                deleted = True
        return deleted


def _yaml_config_service(config_path: Path, content: str) -> ConfigService:
    config_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    return ConfigService(sources=[DefaultConfigSource(), YamlConfigSource(path=config_path)])


def _db_config_service(content: str) -> tuple[ConfigService, WritableDbSource]:
    import yaml

    raw = yaml.safe_load(textwrap.dedent(content).strip() + "\n") or {}
    flat = flatten_config_mapping(raw)
    source = WritableDbSource(
        {
            key: SettingsValue(key=key, value=value, source=SettingsSource.DATABASE)
            for key, value in flat.items()
            if is_database_runtime_key(key)
        }
    )
    return ConfigService(sources=[DefaultConfigSource(), source]), source


@pytest.fixture()
def make_dashboard_client(monkeypatch: pytest.MonkeyPatch):
    def factory(config_service: ConfigService | None = None):
        for var in ("ENABLE_TELEGRAM", "ENABLE_DISCORD"):
            monkeypatch.delenv(var, raising=False)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent.modules.admin_auth import get_current_admin

        from agent.delivery.http.dashboard.router import router

        app = FastAPI()
        app.include_router(router)
        app.state.config_service = config_service or ConfigService(sources=[DefaultConfigSource()])

        async def mock_admin(req: Request) -> str:
            return "test_admin"

        app.dependency_overrides[get_current_admin] = mock_admin
        return TestClient(app)

    return factory


@pytest.fixture()
def dashboard_client(make_dashboard_client):
    return make_dashboard_client()


def test_model_profile_settings_save_validate_and_clear(make_dashboard_client) -> None:
    service, db_source = _db_config_service("llm: {default_model: ''}")
    client = make_dashboard_client(service)
    key = "llm.providers.custom.model_profiles"
    profiles = {"custom-model": {"reasoning_effort_levels": ["low", "high"], "reasoning_effort_default": "high"}}
    response = client.put("/settings", json={"values": {key: profiles}})
    assert response.status_code == 200
    assert db_source.get(key) == profiles
    response = client.put(f"/settings/{key}", json={"value": {"custom-model": {"reasoning_effort_levels": ["low"], "reasoning_effort_default": "high"}}})
    assert response.status_code == 400
    assert db_source.get(key) == profiles
    response = client.put(f"/settings/{key}", json={"value": ""})
    assert response.status_code == 200
    assert db_source.get(key) == {}


def test_skill_sources_save_all_fields_to_database(make_dashboard_client, tmp_path) -> None:
    service, db_source = _db_config_service("llm: {default_model: ''}")
    client = make_dashboard_client(service)
    values = {
        "skills.repository_dir": ".agents/skills",
        "skills.local_execution_mode": "source",
        "skills.cache_root": str(tmp_path / "skill-cache"),
        "skills.additional_roots": [str(tmp_path / "shared-skills")],
    }
    response = client.put("/settings", json={"values": values})
    assert response.status_code == 200, response.text
    service.reload()
    for key, value in values.items():
        assert db_source.get(key) == value
        assert service.get(key) == value


@pytest.mark.parametrize("channel", ["telegram", "discord", "zalo"])
@pytest.mark.parametrize("batch", [False, True])
def test_channel_trim_setting_saves_and_resets_default(make_dashboard_client, channel, batch) -> None:
    service, db_source = _db_config_service("llm: {default_model: ''}")
    client = make_dashboard_client(service)
    key = f"channels.{channel}.context_trim_threshold"
    def update(value):
        return client.put("/settings", json={"values": {key: value}}) if batch else client.put(f"/settings/{key}", json={"value": value})
    assert service.get_int(key) == 50_000
    assert update(12345).status_code == 200
    assert db_source.get(key) == 12345
    assert service.get_int(key) == 12345
    assert update(0).status_code == 400
    assert service.get_int(key) == 12345
    assert update(None).status_code == 200
    assert db_source.get(key) is None
    assert service.get_int(key) == 50_000


class TestDashboardSettingsEndpoints:
    def test_get_config_api_includes_bootstrap_and_excludes_dedicated_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard-api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page_title"] == "Runtime Configuration"
        assert data["settings"]["host"]["restart_required"] is True
        assert data["settings"]["port"]["restart_required"] is True
        assert data["settings"]["enable_web"]["restart_required"] is True
        assert data["settings"]["enable_api"]["restart_required"] is True
        assert data["settings"]["enable_dashboard"]["restart_required"] is True
        assert "bootstrap" in data["by_category"]
        assert "llm.provider" not in data["settings"]
        assert "llm.default_model" not in data["settings"]
        assert "channels.telegram.enabled" not in data["settings"]
        assert "channels" not in data["by_category"]
        assert "workspace.root" not in data["settings"]
        assert "workspace.daytona.enabled" not in data["settings"]
        assert "workspace.modal.enabled" not in data["settings"]
        assert "skills.repository_dir" not in data["settings"]
        assert "database.url" in data["settings"]
        assert "security.jwt_secret" not in data["settings"]
        assert not any(key.startswith("mcp.servers.") for key in data["settings"])
        assert "mcp" not in data["by_category"]

    def test_get_config_api_excludes_mcp_server_settings(
        self,
        make_dashboard_client,
    ) -> None:
        source = StubSource({
            "mcp.servers.filesystem.transport": SettingsValue(
                key="mcp.servers.filesystem.transport",
                value="stdio",
                source=SettingsSource.CONFIG_FILE,
            ),
            "mcp.servers.filesystem.command": SettingsValue(
                key="mcp.servers.filesystem.command",
                value="npx",
                source=SettingsSource.CONFIG_FILE,
            ),
            "mcp.servers.filesystem.enabled": SettingsValue(
                key="mcp.servers.filesystem.enabled",
                value=True,
                source=SettingsSource.CONFIG_FILE,
            ),
        })
        client = make_dashboard_client(
            ConfigService(sources=[DefaultConfigSource(), source])
        )

        resp = client.get("/dashboard-api/config")

        assert resp.status_code == 200
        data = resp.json()
        assert not any(key.startswith("mcp.servers.") for key in data["settings"])
        assert "mcp" not in data["by_category"]
        assert "database.url" in data["settings"]

    def test_get_config_api_excludes_tool_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard-api/config")

        assert resp.status_code == 200
        data = resp.json()
        assert not any(key.startswith("tools.") for key in data["settings"])
        assert "tools" not in data["by_category"]

    def test_get_tools_api_seeds_and_returns_tool_payload(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard-api/tools")

        assert resp.status_code == 200
        data = resp.json()
        assert data["active_nav"] == "tools"
        assert data["page_title"] == "Tool Configuration"
        assert "web_search" in data["tool_config_schemas"]
        assert "generate_image" in data["tool_config_schemas"]
        assert "web_search" in data["tool_config_effective"]
        assert all(key.startswith("tools.") for key in data["settings"])
        assert "tools.web_search.provider" in data["settings"]
        assert "tools.web_search.google_api_key" not in data["settings"]
        assert "tools.web_search.google_connection" in data["settings"]
        assert "tools" in data["by_category"]
        assert set(data["settings_sources"]) == set(data["settings"])

    def test_put_tool_setting_unknown_tool_returns_bad_request(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/tools.nonexistent.field",
            json={"value": "x"},
        )

        assert resp.status_code == 400
        assert "Unknown tool" in resp.json()["detail"]

    def test_put_tool_setting_unknown_field_returns_bad_request(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/tools.web_search.bogus",
            json={"value": "x"},
        )

        assert resp.status_code == 400
        assert "Unknown config field" in resp.json()["detail"]
        assert "Allowed fields" in resp.json()["detail"]

    def test_put_tool_setting_invalid_select_option_returns_bad_request(
        self,
        dashboard_client,
    ) -> None:
        resp = dashboard_client.put(
            "/settings/tools.web_search.provider",
            json={"value": "bogus"},
        )

        assert resp.status_code == 400
        assert "must be one of" in resp.json()["detail"]

    def test_put_tool_setting_saves_valid_value(self, make_dashboard_client) -> None:
        service, db_source = _db_config_service(
            """
            tools:
              web_search:
                provider: auto
            """
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings/tools.web_search.provider",
            json={"value": "google"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "success",
            "key": "tools.web_search.provider",
            "value": "google",
        }
        assert db_source.get("tools.web_search.provider") == "google"

    def test_put_tool_setting_null_deletes_override(self, make_dashboard_client) -> None:
        service, db_source = _db_config_service(
            """
            tools:
              web_search:
                provider: google
                google_api_key: key-123
            """
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings/tools.web_search.google_api_key",
            json={"value": None},
        )

        assert resp.status_code == 200
        assert resp.json()["value"] is None
        flat = db_source.get_all()
        assert flat["tools.web_search.google_api_key"] == "key-123"
        name = flat["tools.web_search.google_connection"]
        assert flat[f"web.connections.{name}.api_key"] == ""
        assert flat["tools.web_search.provider"] == "google"

    def test_put_tool_setting_empty_string_normalizes_to_reset(
        self,
        make_dashboard_client,
    ) -> None:
        service, db_source = _db_config_service(
            """
            tools:
              web_search:
                google_api_key: key-123
            """
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings/tools.web_search.google_api_key",
            json={"value": "   "},
        )

        assert resp.status_code == 200
        assert resp.json()["value"] is None
        flat = db_source.get_all()
        assert flat["tools.web_search.google_api_key"] == "key-123"
        name = flat["tools.web_search.google_connection"]
        assert flat[f"web.connections.{name}.api_key"] == ""

    def test_put_settings_batch_tool_reset_deletes_overrides(
        self,
        make_dashboard_client,
    ) -> None:
        service, db_source = _db_config_service(
            """
            tools:
              web_search:
                provider: google
                google_api_key: key-123
                google_cse_id: cse-1
              generate_image:
                model: gpt-image-1
            """
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings",
            json={
                "values": {
                    "tools.web_search.google_api_key": None,
                    "tools.web_search.provider": "duckduckgo",
                    "tools.generate_image.model": None,
                }
            },
        )

        assert resp.status_code == 200
        assert set(resp.json()["updated"]) == {
            "tools.web_search.google_api_key",
            "tools.web_search.provider",
            "tools.generate_image.model",
        }
        flat = db_source.get_all()
        assert flat["tools.web_search.google_api_key"] == "key-123"
        name = flat["tools.web_search.google_connection"]
        assert flat[f"web.connections.{name}.api_key"] == ""
        assert flat[f"web.connections.{name}.cse_id"] == "cse-1"
        assert "tools.generate_image.model" not in flat
        assert flat["tools.web_search.provider"] == "duckduckgo"
        assert flat["tools.web_search.google_cse_id"] == "cse-1"

    def test_get_backends_api_includes_workspace_backend_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard-api/backends")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_nav"] == "backends"
        assert data["page_title"] == "Workspace Backends"
        assert "workspace.root" in data["settings"]
        assert "workspace.github.root" in data["settings"]
        assert "workspace.daytona.enabled" in data["settings"]
        assert "workspace.modal.enabled" in data["settings"]
        assert "workspace.modal.token_secret" in data["settings"]
        assert "workspace" in data["by_category"]

    def test_get_channels_api_includes_channel_settings(
        self,
        make_dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent.delivery.http.dashboard.routes import dashboard as dashboard_routes

        async def fake_paired_identities() -> list[dict[str, object]]:
            return []

        monkeypatch.setattr(dashboard_routes, "paired_identities", fake_paired_identities)
        client = make_dashboard_client(ConfigService(sources=[DefaultConfigSource()]))

        resp = client.get("/dashboard-api/channels")

        assert resp.status_code == 200
        data = resp.json()
        assert data["identities"] == []
        assert "channels.telegram.bot_token" in data["settings"]
        assert "channels.github.trigger_label" in data["settings"]
        assert "channels.github.webhook_secret" not in data["settings"]
        assert "channels.github.app_id" not in data["settings"]
        assert "channels.github.private_key" not in data["settings"]
        assert "telegram" in data["by_channel"]
        assert "github" in data["by_channel"]

    def test_get_providers_api_shows_provider_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard-api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page_title"] == "Provider Configuration"
        assert "llm.default_provider" in data["settings"]
        assert "llm.provider" not in data["settings"]
        assert "llm.model" not in data["settings"]
        assert data["provider_rows"] == []
        assert data["provider_name_options"] == []

    def test_get_providers_api_returns_provider_model_fields(self, make_dashboard_client) -> None:
        source = StubSource({
            "llm.providers.openai-main.type": SettingsValue(
                key="llm.providers.openai-main.type",
                value="openai_compatible",
                source=SettingsSource.CONFIG_FILE,
            ),
            "llm.providers.openai-main.api_key": SettingsValue(
                key="llm.providers.openai-main.api_key",
                value="openai-key",
                source=SettingsSource.CONFIG_FILE,
            ),
            "llm.providers.openai-main.base_url": SettingsValue(
                key="llm.providers.openai-main.base_url",
                value="https://api.example.com/v1",
                source=SettingsSource.CONFIG_FILE,
            ),
            "llm.providers.openai-main.default_model": SettingsValue(
                key="llm.providers.openai-main.default_model",
                value="openai-default",
                source=SettingsSource.CONFIG_FILE,
            ),
            "llm.providers.openai-main.models": SettingsValue(
                key="llm.providers.openai-main.models",
                value="openai-default,openai-fast",
                source=SettingsSource.CONFIG_FILE,
            ),
        })
        client = make_dashboard_client(ConfigService(sources=[DefaultConfigSource(), source]))

        resp = client.get("/dashboard-api/providers")

        assert resp.status_code == 200
        data = resp.json()
        rows = data["provider_rows"]
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "openai-main"
        assert row["fields"]["default_model"]["key"] == (
            "llm.providers.openai-main.default_model"
        )
        assert row["fields"]["models"]["key"] == "llm.providers.openai-main.models"
        assert row["fields"]["models"]["info"]["value"] == "openai-default,openai-fast"
        assert "models" in data["provider_field_order"]
        assert row["can_set_default"] is True
        assert data["provider_type_options"][0]["value"] == "google"

    @pytest.mark.parametrize(
        ("provider_type", "base_url"),
        [
            ("google", ""),
            ("anthropic", ""),
            ("openai_compatible", "https://api.example.com/v1"),
        ],
    )
    def test_create_provider_saves_expected_fields(
        self,
        make_dashboard_client,
        provider_type: str,
        base_url: str,
    ) -> None:
        service, db_source = _db_config_service(
            """
            llm:
              default_model: ""
              providers: {}
            """
        )
        client = make_dashboard_client(service)

        response = client.post(
            "/dashboard-api/providers",
            json={
                "name": f"{provider_type}-main",
                "type": provider_type,
                "api_key": "provider-key",
                "base_url": base_url,
            },
        )

        assert response.status_code == 200
        assert response.json()["type"] == provider_type

        provider_key = f"llm.providers.{provider_type}-main"
        flat = db_source.get_all()
        assert flat[f"{provider_key}.type"] == provider_type
        assert flat[f"{provider_key}.api_key"] == "provider-key"
        assert flat[f"{provider_key}.default_model"] == ""
        assert flat[f"{provider_key}.models"] == []
        assert flat[f"{provider_key}.enabled"] is True
        if provider_type == "openai_compatible":
            assert flat[f"{provider_key}.base_url"] == "https://api.example.com/v1"
        else:
            assert f"{provider_key}.base_url" not in flat

    def test_create_provider_with_configured_default_model_and_models(
        self,
        make_dashboard_client,
    ) -> None:
        service, db_source = _db_config_service(
            """
            llm:
              default_model: ""
              providers: {}
            """
        )
        client = make_dashboard_client(service)

        response = client.post(
            "/dashboard-api/providers",
            json={
                "name": "google-streamlined",
                "type": "google",
                "api_key": "ai-key-123",
                "default_model": "gemini-2.5-flash",
                "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "created"

        flat = db_source.get_all()
        provider_key = "llm.providers.google-streamlined"
        assert flat[f"{provider_key}.default_model"] == "gemini-2.5-flash"
        assert flat[f"{provider_key}.models"] == ["gemini-2.5-flash", "gemini-2.5-pro"]
        assert flat[f"{provider_key}.enabled"] is True

    def test_create_provider_rejects_duplicate_name(
        self,
        make_dashboard_client,
    ) -> None:
        service, _ = _db_config_service(
            """
            llm:
              default_model: "main/gemini-model"
              providers:
                main:
                  type: "google"
                  api_key: "key"
                  default_model: "gemini-model"
            """
        )
        client = make_dashboard_client(service)

        response = client.post(
            "/dashboard-api/providers",
            json={"name": "main", "type": "google", "api_key": "new-key"},
        )

        assert response.status_code == 409

    def test_create_provider_requires_openai_compatible_base_url(
        self,
        make_dashboard_client,
    ) -> None:
        service, _ = _db_config_service(
            """
            llm:
              default_model: ""
              providers: {}
            """
        )
        client = make_dashboard_client(service)

        response = client.post(
            "/dashboard-api/providers",
            json={"name": "custom", "type": "openai_compatible", "api_key": "key"},
        )

        assert response.status_code == 400
        assert "Base URL" in response.json()["detail"]

    def test_delete_provider_removes_db_block(
        self,
        make_dashboard_client,
    ) -> None:
        service, db_source = _db_config_service(
            """
                llm:
                  default_model: "main/gemini-model"
                  providers:
                    main:
                      type: "google"
                      api_key: "key"
                      default_model: "gemini-model"
                    side:
                      type: "anthropic"
                      api_key: "side-key"
                      default_model: "claude-model"
            """,
        )
        client = make_dashboard_client(service)

        response = client.delete("/dashboard-api/providers/side")

        assert response.status_code == 200
        flat = db_source.get_all()
        assert not any(key.startswith("llm.providers.side.") for key in flat)
        assert any(key.startswith("llm.providers.main.") for key in flat)

    def test_delete_provider_rejects_default_provider(
        self,
        make_dashboard_client,
    ) -> None:
        service, _ = _db_config_service(
            """
            llm:
              default_model: "main/gemini-model"
              providers:
                main:
                  type: "google"
                  api_key: "key"
                  default_model: "gemini-model"
            """
        )
        client = make_dashboard_client(service)

        response = client.delete("/dashboard-api/providers/main")

        assert response.status_code == 400
        assert "Default provider" in response.json()["detail"]

    def test_default_provider_update_requires_default_model(
        self,
        make_dashboard_client,
    ) -> None:
        service, _ = _db_config_service(
            """
            llm:
              default_model: "main/gemini-model"
              providers:
                main:
                  type: "google"
                  api_key: "key"
                  default_model: "gemini-model"
                incomplete:
                  type: "anthropic"
                  api_key: "side-key"
            """
        )
        client = make_dashboard_client(service)

        response = client.put(
            "/settings/llm.default_provider",
            json={"value": "incomplete"},
        )

        assert response.status_code == 400
        assert "default model" in response.json()["detail"].lower()

    def test_default_provider_cannot_be_disabled_from_settings(
        self,
        make_dashboard_client,
    ) -> None:
        service, _ = _db_config_service(
            """
            llm:
              default_model: "main/gemini-model"
              providers:
                main:
                  type: "google"
                  api_key: "key"
                  default_model: "gemini-model"
            """
        )
        client = make_dashboard_client(service)

        response = client.put(
            "/settings/llm.providers.main.enabled",
            json={"value": False},
        )

        assert response.status_code == 400
        assert "enabled" in response.json()["detail"].lower()

    def test_get_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "settings" in data
        assert "channels.telegram.enabled" in data["settings"]
        assert "host" in data["settings"]
        assert data["settings"]["channels.telegram.enabled"]["source"] == "default"

    def test_get_settings_sources(self, dashboard_client) -> None:
        resp = dashboard_client.get("/settings/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert "channels.telegram.enabled" in data["sources"]
        assert "host" in data["sources"]
        assert isinstance(data["sources"]["channels.telegram.enabled"], list)

    def test_put_runtime_setting_saves_successfully(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/channels.telegram.enabled",
            json={"value": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "success",
            "key": "channels.telegram.enabled",
            "value": True,
        }

    def test_put_repository_skills_dir_saves_normalized_value(
        self,
        make_dashboard_client,
    ) -> None:
        service, db_source = _db_config_service(
            """
            skills:
              repository_dir: ".agent/skills"
            """
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings/skills.repository_dir",
            json={"value": "custom/skills"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "success",
            "key": "skills.repository_dir",
            "value": "custom/skills",
        }
        assert db_source.get("skills.repository_dir") == "custom/skills"

    def test_put_workspace_root_saves_to_runtime_database(
        self,
        make_dashboard_client,
    ) -> None:
        service, db_source = _db_config_service(
            """
            workspace:
              root: "~/.k41-agent/workspaces"
              github:
                root: "~/.k41-agent/github-workspaces"
            """
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings/workspace.root",
            json={"value": "~/.k41-agent/workspaces-alt"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "success",
            "key": "workspace.root",
            "value": "~/.k41-agent/workspaces-alt",
        }
        assert db_source.get("workspace.root") == "~/.k41-agent/workspaces-alt"
        assert service.get_str("workspace.root") == "~/.k41-agent/workspaces-alt"

    def test_put_github_workspace_root_saves_to_runtime_database(
        self,
        make_dashboard_client,
    ) -> None:
        service, db_source = _db_config_service(
            """
            workspace:
              github:
                root: "~/.k41-agent/github-workspaces"
            """
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings/workspace.github.root",
            json={"value": "~/.k41-agent/repo-checkouts"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "success",
            "key": "workspace.github.root",
            "value": "~/.k41-agent/repo-checkouts",
        }
        assert db_source.get("workspace.github.root") == "~/.k41-agent/repo-checkouts"
        assert service.get_str("workspace.github.root") == "~/.k41-agent/repo-checkouts"

    @pytest.mark.parametrize(
        "value",
        ["/skills", "../skills", "skills/../x", "skills//x", "skills\\x"],
    )
    def test_put_repository_skills_dir_rejects_unsafe_value(
        self,
        dashboard_client,
        value: str,
    ) -> None:
        resp = dashboard_client.put(
            "/settings/skills.repository_dir",
            json={"value": value},
        )

        assert resp.status_code == 400

    def test_put_settings_batch_saves_successfully(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings",
            json={
                "values": {
                    "channels.telegram.enabled": False,
                    "llm.providers.openai-main.default_model": "sample-model",
                }
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert set(data["updated"]) == {
            "channels.telegram.enabled",
            "llm.providers.openai-main.default_model",
        }

    def test_put_bootstrap_settings_saves_to_yaml(
        self,
        make_dashboard_client,
        tmp_path: Path,
    ) -> None:
        config_path = tmp_path / "config.yml"
        service = _yaml_config_service(
            config_path,
            """
            host: 127.0.0.1
            port: 4141
            enable_dashboard: true
            """,
        )
        client = make_dashboard_client(service)

        resp = client.put(
            "/settings",
            json={
                "values": {
                    "host": " 0.0.0.0 ",
                    "port": "8080",
                    "enable_dashboard": False,
                }
            },
        )

        assert resp.status_code == 200
        assert set(resp.json()["updated"]) == {"host", "port", "enable_dashboard"}

        flat = YamlConfigSource(path=config_path).get_all()
        assert flat["host"] == "0.0.0.0"
        assert flat["port"] == 8080
        assert flat["enable_dashboard"] is False

    def test_put_bootstrap_host_rejects_empty_value(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/host",
            json={"value": "   "},
        )
        assert resp.status_code == 400
        assert "Host cannot be empty" in resp.json()["detail"]

    def test_put_bootstrap_port_rejects_out_of_range(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/port",
            json={"value": 70000},
        )
        assert resp.status_code == 400
        assert "Port must be between 1 and 65535" in resp.json()["detail"]

    def test_put_jwt_secret_returns_bad_request(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/security.jwt_secret",
            json={"value": "secret"},
        )
        assert resp.status_code == 400
        assert "Unsupported runtime setting" in resp.json()["detail"]

    def test_put_provider_specific_setting_saves_successfully(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/llm.providers.openai-main.default_model",
            json={"value": "sample-model"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "success",
            "key": "llm.providers.openai-main.default_model",
            "value": "sample-model",
        }

    def test_put_provider_models_setting_normalizes_to_list(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/llm.providers.openai-main.models",
            json={"value": "model-one, model-two\nmodel-three"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "success",
            "key": "llm.providers.openai-main.models",
            "value": ["model-one", "model-two", "model-three"],
        }

    def test_verify_provider_endpoint_openai_compatible_success(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        fake_resp = httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
            request=httpx.Request("GET", "https://api.openai.com/v1/models"),
        )

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, headers=None):
                return fake_resp

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/providers/verify",
            json={
                "type": "openai_compatible",
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["error_code"] is None
        assert "gpt-4o-mini" in data["models"]
        assert data["suggested_default_model"] == "gpt-4o-mini"
        assert data["latency_ms"] is not None and data["latency_ms"] >= 0

    def test_verify_provider_endpoint_saved_provider(
        self,
        make_dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        service, _ = _db_config_service(
            """
            llm:
              providers:
                custom_openai:
                  type: "openai_compatible"
                  api_key: "saved-key"
                  base_url: "https://api.example.com/v1"
            """
        )
        client = make_dashboard_client(service)

        fake_resp = httpx.Response(
            200,
            json={"data": [{"id": "meta-llama/llama-3.3-70b-instruct"}]},
            request=httpx.Request("GET", "https://api.example.com/v1/models"),
        )

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, headers=None):
                return fake_resp

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = client.post(
            "/dashboard-api/providers/verify",
            json={"name": "custom_openai"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["models"] == ["meta-llama/llama-3.3-70b-instruct"]
        assert data["suggested_default_model"] == "meta-llama/llama-3.3-70b-instruct"

    def test_verify_provider_endpoint_not_found(self, dashboard_client) -> None:
        resp = dashboard_client.post(
            "/dashboard-api/providers/verify",
            json={"name": "missing_provider"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "INVALID_CONFIG"
        assert "not found" in data["message"].lower()

    def test_verify_provider_endpoint_auth_failed(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        fake_resp = httpx.Response(
            401,
            text="Invalid key",
            request=httpx.Request("GET", "https://api.openai.com/v1/models"),
        )

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, headers=None):
                raise httpx.HTTPStatusError("Unauthorized", request=fake_resp.request, response=fake_resp)

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/providers/verify",
            json={
                "type": "openai_compatible",
                "api_key": "invalid-key",
                "base_url": "https://api.openai.com/v1",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "AUTH_FAILED"
        assert "authentication" in data["message"].lower()

    def test_verify_provider_endpoint_timeout(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, headers=None):
                raise httpx.ReadTimeout("Timed out")

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/providers/verify",
            json={
                "type": "openai_compatible",
                "api_key": "timeout-key",
                "base_url": "https://api.openai.com/v1",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "TIMEOUT"

    def test_verify_provider_endpoint_connection_error(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, headers=None):
                raise httpx.ConnectError("Could not resolve host")

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/providers/verify",
            json={
                "type": "openai_compatible",
                "api_key": "any-key",
                "base_url": "https://unreachable.example.com",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "CONNECTION_ERROR"

    def test_verify_provider_endpoint_invalid_config(self, dashboard_client) -> None:
        # Missing type
        resp = dashboard_client.post("/dashboard-api/providers/verify", json={"api_key": "key"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert resp.json()["error_code"] == "INVALID_CONFIG"

        # Missing base_url for openai_compatible
        resp2 = dashboard_client.post(
            "/dashboard-api/providers/verify",
            json={"type": "openai_compatible", "api_key": "key", "base_url": ""},
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is False
        assert resp2.json()["error_code"] == "INVALID_CONFIG"

    def test_verify_web_connection_endpoint_success(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

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

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/web-connections/verify",
            json={"type": "tavily", "api_key": "tvly-test-key"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["error_code"] is None
        assert "Tavily" in data["message"]
        assert data["latency_ms"] >= 0

    def test_verify_web_connection_endpoint_saved_connection(
        self,
        make_dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        service, _ = _db_config_service(
            """
            web:
              connections:
                my_tavily:
                  type: "tavily"
                  api_key: "saved-tvly-key"
            """
        )
        client = make_dashboard_client(service)

        fake_resp = httpx.Response(
            200,
            json={"results": []},
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

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = client.post(
            "/dashboard-api/web-connections/verify",
            json={"name": "my_tavily"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["error_code"] is None

    def test_verify_web_connection_endpoint_auth_failed(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        fake_resp = httpx.Response(
            401,
            text="Unauthorized key",
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
                raise httpx.HTTPStatusError("Unauthorized", request=fake_resp.request, response=fake_resp)

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/web-connections/verify",
            json={"type": "tavily", "api_key": "bad-key"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "AUTH_FAILED"

    def test_verify_web_connection_endpoint_timeout(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                raise httpx.ReadTimeout("Timeout")

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/web-connections/verify",
            json={"type": "tavily", "api_key": "slow-key"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "TIMEOUT"

    def test_verify_web_connection_endpoint_invalid_config(self, dashboard_client) -> None:
        # Unknown type
        resp = dashboard_client.post(
            "/dashboard-api/web-connections/verify",
            json={"type": "unknown_web", "api_key": "k"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert resp.json()["error_code"] == "INVALID_CONFIG"

        # Missing api_key
        resp2 = dashboard_client.post(
            "/dashboard-api/web-connections/verify",
            json={"type": "tavily", "api_key": ""},
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is False
        assert resp2.json()["error_code"] == "INVALID_CONFIG"

    def test_verify_decision_provider_endpoint_success(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        fake_resp = httpx.Response(
            200,
            json={"success": True, "result": {"answers": {"ping": {"noul": 0.0}}}},
            request=httpx.Request("POST", "https://api.cloudflare.com/client/v4/accounts/cf-account-id/ai/run/@cf/cloudflare/clef-flash"),
        )

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, headers=None, **kwargs):
                assert "ai/run/" in str(url)
                return fake_resp

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/decision-providers/verify",
            json={
                "type": "cloudflare",
                "fields": {
                    "account_id": "cf-account-id",
                    "api_token": "cf-api-token",
                },
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["error_code"] is None
        assert "Cloudflare" in data["message"]
        assert data["latency_ms"] >= 0

    def test_verify_decision_provider_endpoint_saved_provider(
        self,
        make_dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        service, _ = _db_config_service(
            """
            decision:
              providers:
                my_cf:
                  type: "cloudflare"
                  account_id: "stored-acc"
                  api_token: "stored-token"
            """
        )
        client = make_dashboard_client(service)

        fake_resp = httpx.Response(
            200,
            json={"success": True, "result": {"answers": {"ping": {"noul": 0.0}}}},
            request=httpx.Request("POST", "https://api.cloudflare.com/client/v4/accounts/stored-acc/ai/run/@cf/cloudflare/clef-flash"),
        )

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, headers=None, **kwargs):
                assert "ai/run/" in str(url)
                return fake_resp

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = client.post(
            "/dashboard-api/decision-providers/verify",
            json={"name": "my_cf"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_verify_decision_provider_endpoint_auth_failed(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        fake_resp = httpx.Response(
            401,
            json={"errors": [{"message": "Invalid token"}]},
            request=httpx.Request("POST", "https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/cloudflare/clef-flash"),
        )

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, headers=None, **kwargs):
                raise httpx.HTTPStatusError("Unauthorized", request=fake_resp.request, response=fake_resp)

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/decision-providers/verify",
            json={
                "type": "cloudflare",
                "fields": {
                    "account_id": "acc",
                    "api_token": "bad-token",
                },
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "AUTH_FAILED"

    def test_verify_decision_provider_endpoint_timeout(
        self,
        dashboard_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import httpx

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, headers=None, **kwargs):
                raise httpx.ReadTimeout("Timeout")

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        resp = dashboard_client.post(
            "/dashboard-api/decision-providers/verify",
            json={
                "type": "cloudflare",
                "fields": {
                    "account_id": "acc",
                    "api_token": "token",
                },
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error_code"] == "TIMEOUT"

    def test_verify_decision_provider_endpoint_invalid_config(self, dashboard_client) -> None:
        # Missing account_id
        resp = dashboard_client.post(
            "/dashboard-api/decision-providers/verify",
            json={"type": "cloudflare", "fields": {"api_token": "token"}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert resp.json()["error_code"] == "INVALID_CONFIG"

        # Missing api_token
        resp2 = dashboard_client.post(
            "/dashboard-api/decision-providers/verify",
            json={"type": "cloudflare", "fields": {"account_id": "acc"}},
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is False
        assert resp2.json()["error_code"] == "INVALID_CONFIG"

