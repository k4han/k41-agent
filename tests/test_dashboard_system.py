from __future__ import annotations

from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.bootstrap.update import ReleaseInfo, UpdateError
from agent.bootstrap.update_state import read_update_status, update_lock, write_update_status
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


def test_trigger_system_update_managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _create_client()
    monkeypatch.setattr(system_module, "is_managed_install", lambda: True)

    mock_install = type("Install", (), {
        "agent_home": tmp_path,
        "python_exe": Path("fake/python.exe"),
        "app_dir": Path("fake/app"),
    })()
    monkeypatch.setattr(system_module, "resolve_managed_install", lambda: mock_install)
    monkeypatch.setattr(system_module, "is_systemd_service_active", lambda: False)

    spawn_called = False

    def mock_spawn(cmd, log_file, cwd):
        nonlocal spawn_called
        spawn_called = True
        assert "--yes" in cmd
        assert "--delay" in cmd
        assert "--update-id" in cmd
        assert cwd == mock_install.agent_home

    monkeypatch.setattr(system_module, "spawn_detached_process", mock_spawn)

    resp = client.post("/dashboard-api/system/update")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert spawn_called is True
    update_id = resp.json()["update_id"]
    status = client.get(f"/dashboard-api/system/update/status?update_id={update_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert client.get("/dashboard-api/system/update/status?update_id=other").status_code == 404
    assert client.post("/dashboard-api/system/update").status_code == 409


@pytest.fixture
def managed_install(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    install = SimpleNamespace(agent_home=tmp_path, app_dir=tmp_path / "app", python_exe=tmp_path / "python.exe")
    monkeypatch.setattr(system_module, "is_managed_install", lambda: True)
    monkeypatch.setattr(system_module, "resolve_managed_install", lambda: install)
    monkeypatch.setattr(system_module, "is_systemd_service_active", lambda: False)
    return install


def test_update_launch_error_is_persisted_and_allows_retry(managed_install, monkeypatch) -> None:
    client = _create_client()

    def fail_spawn(*args, **kwargs):
        raise OSError("cannot launch updater")

    monkeypatch.setattr(system_module, "spawn_detached_process", fail_spawn)
    response = client.post("/dashboard-api/system/update")
    assert response.status_code == 500
    assert read_update_status(managed_install.agent_home)["error"] == "cannot launch updater"
    monkeypatch.setattr(system_module, "spawn_detached_process", lambda *args, **kwargs: None)
    assert client.post("/dashboard-api/system/update").status_code == 200


def test_update_status_survives_new_application_instance(managed_install) -> None:
    write_update_status(managed_install.agent_home, {"update_id": "job", "status": "failed", "error": "sync failed"})
    response = _create_client().get("/dashboard-api/system/update/status?update_id=job")
    assert response.status_code == 200
    assert response.json()["error"] == "sync failed"


def test_dashboard_does_not_start_update_while_cli_holds_lock(managed_install, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(system_module, "spawn_detached_process", lambda *args, **kwargs: calls.append(args))
    with update_lock(managed_install.agent_home):
        assert _create_client().post("/dashboard-api/system/update").status_code == 409
    assert calls == []


def test_systemd_updater_runs_in_separate_scope(managed_install, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(system_module, "is_systemd_service_active", lambda: True)
    monkeypatch.setattr(system_module.shutil, "which", lambda command: "/usr/bin/systemd-run")
    monkeypatch.setattr(system_module, "spawn_detached_process", lambda *args, **kwargs: calls.append((args, kwargs)))
    response = _create_client().post("/dashboard-api/system/update")
    assert response.status_code == 200
    command = calls[0][0][0]
    assert command[:4] == ["/usr/bin/systemd-run", "--user", "--scope", "--quiet"]
    assert command[4] == f"--unit=k41-agent-update-{response.json()['update_id']}"
    assert command[5] == "--"
    assert calls[0][1]["cwd"] == managed_install.agent_home


def test_systemd_update_rejects_missing_scope_launcher(managed_install, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(system_module, "is_systemd_service_active", lambda: True)
    monkeypatch.setattr(system_module.shutil, "which", lambda command: None)
    monkeypatch.setattr(system_module, "spawn_detached_process", lambda *args, **kwargs: calls.append(args))
    response = _create_client().post("/dashboard-api/system/update")
    assert response.status_code == 500
    assert "systemd-run" in response.json()["detail"]
    assert calls == []


def test_update_status_returns_client_error_for_development_install(monkeypatch) -> None:
    def unmanaged(*args, **kwargs):
        raise UpdateError("not a managed install")

    monkeypatch.setattr(system_module, "resolve_managed_install", unmanaged)
    monkeypatch.setattr(system_module, "detect_agent_home", unmanaged)
    response = _create_client().get("/dashboard-api/system/update/status?update_id=job")
    assert response.status_code == 400


def test_update_failure_status_remains_readable_with_broken_metadata(managed_install, monkeypatch) -> None:
    write_update_status(managed_install.agent_home, {"update_id": "job", "status": "failed", "error": "invalid project metadata"})

    def invalid_install():
        raise UpdateError("invalid project metadata")

    monkeypatch.setattr(system_module, "resolve_managed_install", invalid_install)
    monkeypatch.setattr(system_module, "detect_agent_home", lambda: managed_install.agent_home)
    client = _create_client()
    response = client.get("/dashboard-api/system/update/status?update_id=job")
    assert response.status_code == 200
    assert response.json()["error"] == "invalid project metadata"
    assert client.get("/dashboard-api/system/update/status?update_id=other").status_code == 400


def test_reused_updater_pid_does_not_block_new_update(managed_install, monkeypatch) -> None:
    from agent.bootstrap import update_state

    write_update_status(managed_install.agent_home, {
        "update_id": "old", "status": "running", "pid": 123, "process_started_at": 100.0,
    })
    monkeypatch.setattr(update_state, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(update_state, "get_process_start_time", lambda pid: 200.0)
    monkeypatch.setattr(system_module, "spawn_detached_process", lambda *args, **kwargs: None)
    response = _create_client().post("/dashboard-api/system/update")
    assert response.status_code == 200
    assert response.json()["update_id"] != "old"
