from __future__ import annotations

import pytest
from fastapi import Request

from agent.shared.config.constants import (
    PLATFORM_MANAGED_ENV_VARS,
    PLATFORM_MANAGED_KEYS,
    is_database_runtime_key,
    is_platform_managed_key,
    is_runtime_key,
)


@pytest.fixture()
def make_dashboard_client(monkeypatch: pytest.MonkeyPatch):
    def factory(config_service=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent.modules.admin_auth import get_current_admin
        from agent.delivery.http.dashboard.router import router
        from agent.shared.config import ConfigService as _ConfigService
        from agent.shared.config.default_source import DefaultConfigSource

        app = FastAPI()
        app.include_router(router)
        app.state.config_service = config_service or _ConfigService(
            sources=[DefaultConfigSource()]
        )

        async def mock_admin(req: Request) -> str:
            return "test_admin"

        app.dependency_overrides[get_current_admin] = mock_admin
        return TestClient(app)

    return factory


def test_platform_managed_keys_are_env_only() -> None:
    expected = {
        "channels.github.app_id",
        "channels.github.app_slug",
        "channels.github.private_key",
        "channels.github.private_key_path",
        "channels.github.webhook_secret",
        "google_calendar.client_id",
        "google_calendar.client_secret",
        "google_calendar.redirect_uri",
    }
    assert set(PLATFORM_MANAGED_KEYS) == expected
    for key in expected:
        assert is_platform_managed_key(key)
        assert not is_runtime_key(key)
        assert not is_database_runtime_key(key)
        assert key in PLATFORM_MANAGED_ENV_VARS

    # User preferences stay runtime-writable (BYOK model).
    assert is_runtime_key("channels.github.enabled")
    assert is_runtime_key("channels.github.default_agent")
    assert is_runtime_key("channels.github.trigger_label")
    assert is_runtime_key("google_calendar.enabled")


def test_github_settings_read_platform_env(monkeypatch) -> None:
    from agent.modules.github.config import get_github_settings

    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_SLUG", "k41-agent")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "inline-key")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "hook-secret")

    settings = get_github_settings()
    assert settings.app_id == "12345"
    assert settings.app_slug == "k41-agent"
    assert settings.private_key == "inline-key"
    assert settings.webhook_secret == "hook-secret"


def test_google_settings_read_platform_env(monkeypatch) -> None:
    from agent.modules.google_calendar.config import get_google_calendar_settings

    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", "csecret")
    monkeypatch.setenv(
        "GOOGLE_CALENDAR_REDIRECT_URI", "http://localhost:4141/integrations/google/callback"
    )

    settings = get_google_calendar_settings()
    assert settings.client_id == "cid"
    assert settings.client_secret == "csecret"
    assert settings.is_configured is True


def test_github_settings_fall_back_to_operator_yaml(monkeypatch, tmp_path) -> None:
    from agent.shared.config import ConfigService
    from agent.shared.config.default_source import DefaultConfigSource
    from agent.shared.config.yaml_source import YamlConfigSource
    from agent.bootstrap.container import AppContainer, set_active_container

    for var in (
        "GITHUB_APP_ID",
        "GITHUB_APP_SLUG",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "channels:\n"
        "  github:\n"
        "    app_id: '999'\n"
        "    webhook_secret: 'hook'\n",
        encoding="utf-8",
    )
    service = ConfigService(
        sources=[DefaultConfigSource(), YamlConfigSource(path=yaml_path)]
    )
    set_active_container(AppContainer(config_service=service))

    from agent.modules.github.config import get_github_settings

    settings = get_github_settings()
    assert settings.app_id == "999"
    assert settings.webhook_secret == "hook"


@pytest.mark.asyncio
async def test_github_sync_error_names_missing_env(monkeypatch, tmp_path) -> None:
    import pytest

    from agent.modules.github.service import GitHubAutomationService

    for var in (
        "GITHUB_APP_ID",
        "GITHUB_APP_SLUG",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    service = GitHubAutomationService.__new__(GitHubAutomationService)
    with pytest.raises(ValueError, match="GITHUB_APP_ID"):
        await service.sync_installations()


def test_dashboard_rejects_platform_keys(make_dashboard_client) -> None:
    from agent.shared.config import ConfigService
    from agent.shared.config.default_source import DefaultConfigSource

    client = make_dashboard_client(ConfigService(sources=[DefaultConfigSource()]))

    resp = client.put(
        "/settings",
        json={"values": {"channels.github.app_id": "123"}},
    )
    assert resp.status_code == 403

    resp = client.put(
        "/settings/channels.github.private_key",
        json={"value": "secret"},
    )
    assert resp.status_code == 403

    resp = client.put(
        "/dashboard-api/google-calendar/config",
        json={"client_secret": "secret"},
    )
    assert resp.status_code == 403
