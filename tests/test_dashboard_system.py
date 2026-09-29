from __future__ import annotations

from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.bootstrap.update import ReleaseInfo
from agent.delivery.http.dashboard.router import router as dashboard_router
from agent.delivery.http.dashboard.routes import system as system_module
from agent.modules.admin_auth import get_current_admin
from agent.modules.channels import ChannelManager
from agent.shared.config import ConfigService
from agent.shared.config.default_source import DefaultConfigSource


def _create_client() -> TestClient:
    app = FastAPI()
    app.state.channel_manager = ChannelManager()
    app.state.config_service = ConfigService(sources=[DefaultConfigSource()])
    app.include_router(dashboard_router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app)


def test_get_system_version_cached_and_force(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _create_client()

    # Reset cache
    system_module._cached_version_info = None
    system_module._last_check_time = 0.0

    call_count = 0

    def mock_fetch(**kwargs):
        nonlocal call_count
        call_count += 1
        return ReleaseInfo(
            tag_name="v0.2.0",
            version="0.2.0",
            asset_url="https://example.test/k41.zip",
            html_url="https://github.com/k4han/k41-agent/releases/tag/v0.2.0",
            name="Version 0.2.0",
            body="New features and improvements",
            published_at="2026-09-27T00:00:00Z",
        )

    monkeypatch.setattr(system_module, "fetch_latest_release", mock_fetch)
    monkeypatch.setattr(system_module, "is_managed_install", lambda: True)

    resp = client.get("/dashboard-api/system/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest_version"] == "0.2.0"
    assert data["has_update"] is True
    assert data["release_name"] == "Version 0.2.0"
    assert data["release_notes"] == "New features and improvements"
    assert data["is_managed_install"] is True
    assert data["install_type"] == "managed"
    assert call_count == 1

    # Second call should use cache
    resp2 = client.get("/dashboard-api/system/version")
    assert resp2.status_code == 200
    assert call_count == 1

    # Force call should bypass cache
    resp3 = client.get("/dashboard-api/system/version?force=true")
    assert resp3.status_code == 200
    assert call_count == 2


def test_trigger_system_update_rejects_unmanaged(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _create_client()
    monkeypatch.setattr(system_module, "is_managed_install", lambda: False)

    resp = client.post("/dashboard-api/system/update")
    assert resp.status_code == 400
    assert "development mode" in resp.json()["detail"].lower()


def test_trigger_system_update_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _create_client()
    monkeypatch.setattr(system_module, "is_managed_install", lambda: True)

    mock_install = type("Install", (), {
        "python_exe": Path("fake/python.exe"),
        "app_dir": Path("fake/app"),
    })()
    monkeypatch.setattr(system_module, "resolve_managed_install", lambda: mock_install)

    spawn_called = False

    def mock_spawn(cmd, log_file, cwd):
        nonlocal spawn_called
        spawn_called = True
        assert "--yes" in cmd
        assert "--delay" in cmd
        assert cwd == mock_install.app_dir

    monkeypatch.setattr(system_module, "spawn_detached_process", mock_spawn)

    resp = client.post("/dashboard-api/system/update")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert spawn_called is True
