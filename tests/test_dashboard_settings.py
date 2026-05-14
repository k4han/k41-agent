"""Tests for dashboard settings endpoints."""

from __future__ import annotations

import pytest
from fastapi import Request

from agent.shared.config import ConfigService
from agent.shared.config.default_source import DefaultConfigSource


@pytest.fixture()
def dashboard_client(monkeypatch: pytest.MonkeyPatch):
    """Create a TestClient with config_service wired up."""
    for var in ("ENABLE_TELEGRAM", "ENABLE_DISCORD"):
        monkeypatch.delenv(var, raising=False)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent.modules.admin_auth import get_current_admin

    from agent.delivery.http.dashboard.router import router

    app = FastAPI()
    app.include_router(router)

    # Wire up config_service
    config_service = ConfigService(sources=[DefaultConfigSource()])
    app.state.config_service = config_service

    # Override auth dependency to bypass it in tests
    async def mock_admin(req: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app)


class TestDashboardSettingsEndpoints:
    def test_get_config_page_excludes_provider_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/config")
        assert resp.status_code == 200
        assert "Runtime Configuration" in resp.text
        assert "llm.provider" not in resp.text
        assert "channels.telegram.enabled" in resp.text

    def test_get_providers_page_shows_provider_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/providers")
        assert resp.status_code == 200
        assert "Provider Configuration" in resp.text
        assert "llm.default_provider" in resp.text
        assert 'id="input-llm.default_provider"' in resp.text
        assert "Auto select enabled provider" in resp.text
        assert 'data-key="llm.provider"' not in resp.text
        assert 'data-key="llm.model"' not in resp.text
        assert "Provider Config" in resp.text
        assert "<th style=\"width: 130px;\">Provider</th>" in resp.text
        assert "No providers found in config" in resp.text

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
