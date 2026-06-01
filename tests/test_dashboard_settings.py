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


class TestDashboardSettingsEndpoints:
    def test_get_config_api_excludes_provider_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard-api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page_title"] == "Runtime Configuration"
        assert "llm.provider" not in data["settings"]
        assert "channels.telegram.enabled" in data["settings"]
        assert "channels" in data["by_category"]

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
        assert "host" not in data["settings"]
        assert data["settings"]["channels.telegram.enabled"]["source"] == "default"

    def test_get_settings_sources(self, dashboard_client) -> None:
        resp = dashboard_client.get("/settings/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert "channels.telegram.enabled" in data["sources"]
        assert "host" not in data["sources"]
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

    def test_put_settings_batch_rejects_bootstrap_keys(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings",
            json={"values": {"host": "0.0.0.0"}},
        )
        assert resp.status_code == 400
        assert "Unsupported runtime setting" in resp.json()["detail"]

    def test_put_bootstrap_setting_returns_bad_request(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/settings/host",
            json={"value": "false"},
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
