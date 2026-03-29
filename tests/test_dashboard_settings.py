"""Tests for dashboard settings endpoints."""

from __future__ import annotations

import pytest
from pytest import MonkeyPatch

from agent.modules.settings.application.settings_service import SettingsService
from agent.modules.settings.infrastructure.default_repository import (
    DefaultSettingsRepository,
)


@pytest.fixture()
def dashboard_client(monkeypatch: MonkeyPatch):
    """Create a TestClient with settings_service wired up."""
    import agent.modules.settings.public as pub
    monkeypatch.setattr(pub, "_settings_service", None)

    # Clear env for predictable defaults
    for var in ("HOST", "PORT", "ENABLE_WEB", "ENABLE_API", "ENABLE_DASHBOARD",
                 "ENABLE_TELEGRAM", "ENABLE_DISCORD"):
        monkeypatch.delenv(var, raising=False)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent.delivery.http.dashboard.router import router

    app = FastAPI()
    app.include_router(router)

    # Wire up settings_service
    settings_service = SettingsService(repositories=[DefaultSettingsRepository()])
    app.state.settings_service = settings_service

    return TestClient(app)


class TestDashboardSettingsEndpoints:
    def test_get_settings(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "settings" in data
        assert "host" in data["settings"]
        assert data["settings"]["host"]["source"] == "default"

    def test_get_settings_sources(self, dashboard_client) -> None:
        resp = dashboard_client.get("/dashboard/settings/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert "host" in data["sources"]
        assert isinstance(data["sources"]["host"], list)

    def test_put_setting(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/dashboard/settings/host",
            json={"value": "127.0.0.1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "host"
        assert data["value"] == "127.0.0.1"

    def test_put_setting_nested_key(self, dashboard_client) -> None:
        resp = dashboard_client.put(
            "/dashboard/settings/channels.telegram.enabled",
            json={"value": "false"},
        )
        assert resp.status_code == 200
        assert resp.json()["key"] == "channels.telegram.enabled"
