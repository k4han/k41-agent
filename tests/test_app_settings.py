from pytest import MonkeyPatch

from agent.bootstrap.settings import AppSettings


def test_app_settings_load_enable_flags_from_env(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("ENABLE_WEB", "true")
    monkeypatch.setenv("ENABLE_API", "false")
    monkeypatch.setenv("ENABLE_DASHBOARD", "true")
    monkeypatch.setenv("ENABLE_TELEGRAM", "true")
    monkeypatch.setenv("ENABLE_DISCORD", "false")

    settings = AppSettings.from_env()

    assert settings.enable_web is True
    assert settings.enable_api is False
    assert settings.enable_dashboard is True
    assert settings.service_boot_flags == {
        "telegram": True,
        "discord": False,
    }


def test_app_settings_default_service_flags_are_enabled(monkeypatch: MonkeyPatch):
    monkeypatch.delenv("ENABLE_TELEGRAM", raising=False)
    monkeypatch.delenv("ENABLE_DISCORD", raising=False)

    settings = AppSettings.from_env()

    assert settings.service_boot_flags["telegram"] is True
    assert settings.service_boot_flags["discord"] is True
