"""Tests for the settings module — SettingsService, repositories, and public API."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from agent.modules.settings.application.settings_service import SettingsService
from agent.modules.settings.domain.settings_value import (
    AppSettingsData,
    SettingsSource,
    SettingsValue,
)
from agent.modules.settings.infrastructure.default_repository import (
    DefaultSettingsRepository,
)
from agent.modules.settings.infrastructure.env_repository import EnvSettingsRepository


# =====================================================================
# Helpers
# =====================================================================


class StubRepository:
    """In-memory settings repository for testing."""

    def __init__(self, entries: dict[str, SettingsValue]) -> None:
        self._entries = entries

    def get_all(self) -> dict[str, SettingsValue]:
        return dict(self._entries)

    def get(self, key: str) -> SettingsValue | None:
        return self._entries.get(key)


def _sv(key: str, value: object, source: SettingsSource) -> SettingsValue:
    return SettingsValue(key=key, value=value, source=source)


# =====================================================================
# DefaultSettingsRepository
# =====================================================================


class TestDefaultSettingsRepository:
    def test_returns_all_known_keys(self) -> None:
        repo = DefaultSettingsRepository()
        all_settings = repo.get_all()

        assert "host" in all_settings
        assert "port" in all_settings
        assert "enable_web" in all_settings
        assert "channels.telegram.enabled" in all_settings
        assert "channels.discord.enabled" in all_settings

    def test_all_values_have_default_source(self) -> None:
        repo = DefaultSettingsRepository()
        for sv in repo.get_all().values():
            assert sv.source == SettingsSource.DEFAULT

    def test_get_existing_key(self) -> None:
        repo = DefaultSettingsRepository()
        val = repo.get("host")
        assert val is not None
        assert val.value == "0.0.0.0"

    def test_get_missing_key(self) -> None:
        repo = DefaultSettingsRepository()
        assert repo.get("nonexistent") is None


# =====================================================================
# EnvSettingsRepository
# =====================================================================


class TestEnvSettingsRepository:
    def test_reads_host_and_port(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "9000")

        repo = EnvSettingsRepository()
        all_settings = repo.get_all()

        assert all_settings["host"].value == "localhost"
        assert all_settings["port"].value == 9000

    def test_reads_enable_flags(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("ENABLE_WEB", "false")
        monkeypatch.setenv("ENABLE_API", "true")

        repo = EnvSettingsRepository()
        all_settings = repo.get_all()

        assert all_settings["enable_web"].value is False
        assert all_settings["enable_api"].value is True

    def test_reads_channel_flags(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("ENABLE_TELEGRAM", "0")
        monkeypatch.setenv("ENABLE_DISCORD", "yes")

        repo = EnvSettingsRepository()
        all_settings = repo.get_all()

        assert all_settings["channels.telegram.enabled"].value is False
        assert all_settings["channels.discord.enabled"].value is True

    def test_missing_env_vars_not_in_results(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("HOST", raising=False)
        monkeypatch.delenv("PORT", raising=False)
        monkeypatch.delenv("ENABLE_WEB", raising=False)
        monkeypatch.delenv("ENABLE_API", raising=False)
        monkeypatch.delenv("ENABLE_DASHBOARD", raising=False)
        monkeypatch.delenv("ENABLE_TELEGRAM", raising=False)
        monkeypatch.delenv("ENABLE_DISCORD", raising=False)

        repo = EnvSettingsRepository()
        assert repo.get_all() == {}

    def test_all_values_have_env_source(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "0.0.0.0")
        repo = EnvSettingsRepository()
        for sv in repo.get_all().values():
            assert sv.source == SettingsSource.ENV_OVERRIDE


# =====================================================================
# ConfigFileRepository
# =====================================================================


class TestConfigFileRepository:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        from agent.modules.settings.infrastructure.config_file_repository import (
            ConfigFileRepository,
        )

        repo = ConfigFileRepository(path=tmp_path / "nonexistent.yml")
        assert repo.get_all() == {}

    def test_reads_flat_values(self, tmp_path: Path) -> None:
        from agent.modules.settings.infrastructure.config_file_repository import (
            ConfigFileRepository,
        )

        cfg = tmp_path / "config.yml"
        cfg.write_text(
            textwrap.dedent("""\
            host: 127.0.0.1
            port: 3000
            """),
            encoding="utf-8",
        )

        repo = ConfigFileRepository(path=cfg)
        all_settings = repo.get_all()

        assert all_settings["host"].value == "127.0.0.1"
        assert all_settings["host"].source == SettingsSource.CONFIG_FILE
        assert all_settings["port"].value == 3000

    def test_reads_nested_values(self, tmp_path: Path) -> None:
        from agent.modules.settings.infrastructure.config_file_repository import (
            ConfigFileRepository,
        )

        cfg = tmp_path / "config.yml"
        cfg.write_text(
            textwrap.dedent("""\
            channels:
              telegram:
                enabled: false
              discord:
                enabled: true
            """),
            encoding="utf-8",
        )

        repo = ConfigFileRepository(path=cfg)
        all_settings = repo.get_all()

        assert all_settings["channels.telegram.enabled"].value is False
        assert all_settings["channels.discord.enabled"].value is True

    def test_reload_clears_cache(self, tmp_path: Path) -> None:
        from agent.modules.settings.infrastructure.config_file_repository import (
            ConfigFileRepository,
        )

        cfg = tmp_path / "config.yml"
        cfg.write_text("host: first\n", encoding="utf-8")

        repo = ConfigFileRepository(path=cfg)
        assert repo.get("host").value == "first"

        cfg.write_text("host: second\n", encoding="utf-8")
        repo.reload()
        assert repo.get("host").value == "second"


# =====================================================================
# SettingsService — precedence merge
# =====================================================================


class TestSettingsService:
    def test_higher_precedence_wins(self) -> None:
        low = StubRepository({
            "host": _sv("host", "default-host", SettingsSource.DEFAULT),
            "port": _sv("port", 8000, SettingsSource.DEFAULT),
        })
        high = StubRepository({
            "host": _sv("host", "env-host", SettingsSource.ENV_OVERRIDE),
        })

        service = SettingsService(repositories=[low, high])

        assert service.get_effective("host").value == "env-host"
        assert service.get_effective("host").source == SettingsSource.ENV_OVERRIDE
        assert service.get_effective("port").value == 8000

    def test_list_all_merges(self) -> None:
        low = StubRepository({
            "a": _sv("a", 1, SettingsSource.DEFAULT),
            "b": _sv("b", 2, SettingsSource.DEFAULT),
        })
        high = StubRepository({
            "b": _sv("b", 20, SettingsSource.CONFIG_FILE),
            "c": _sv("c", 30, SettingsSource.CONFIG_FILE),
        })

        service = SettingsService(repositories=[low, high])
        merged = service.list_all()

        assert merged["a"].value == 1
        assert merged["b"].value == 20
        assert merged["c"].value == 30

    def test_get_app_settings_uses_merged_values(self) -> None:
        defaults = StubRepository({
            "host": _sv("host", "0.0.0.0", SettingsSource.DEFAULT),
            "port": _sv("port", 8000, SettingsSource.DEFAULT),
            "enable_web": _sv("enable_web", True, SettingsSource.DEFAULT),
            "enable_api": _sv("enable_api", True, SettingsSource.DEFAULT),
            "enable_dashboard": _sv("enable_dashboard", True, SettingsSource.DEFAULT),
            "channels.telegram.enabled": _sv("channels.telegram.enabled", True, SettingsSource.DEFAULT),
            "channels.discord.enabled": _sv("channels.discord.enabled", True, SettingsSource.DEFAULT),
        })
        env = StubRepository({
            "port": _sv("port", 3000, SettingsSource.ENV_OVERRIDE),
            "enable_api": _sv("enable_api", False, SettingsSource.ENV_OVERRIDE),
            "channels.discord.enabled": _sv("channels.discord.enabled", False, SettingsSource.ENV_OVERRIDE),
        })

        service = SettingsService(repositories=[defaults, env])
        app_settings = service.get_app_settings()

        assert app_settings.host == "0.0.0.0"
        assert app_settings.port == 3000
        assert app_settings.enable_web is True
        assert app_settings.enable_api is False
        assert app_settings.service_boot_flags["telegram"] is True
        assert app_settings.service_boot_flags["discord"] is False

    def test_get_effective_missing_key_returns_none(self) -> None:
        service = SettingsService(repositories=[])
        assert service.get_effective("nonexistent") is None

    def test_get_settings_overview_format(self) -> None:
        repo = StubRepository({
            "host": _sv("host", "localhost", SettingsSource.CONFIG_FILE),
        })
        service = SettingsService(repositories=[repo])
        overview = service.get_settings_overview()

        assert "host" in overview
        assert overview["host"]["value"] == "localhost"
        assert overview["host"]["source"] == "config_file"

    def test_get_settings_sources_format(self) -> None:
        low = StubRepository({
            "host": _sv("host", "default", SettingsSource.DEFAULT),
        })
        high = StubRepository({
            "host": _sv("host", "env", SettingsSource.ENV_OVERRIDE),
        })
        service = SettingsService(repositories=[low, high])
        sources = service.get_settings_sources()

        assert "host" in sources
        assert len(sources["host"]) == 2
        assert sources["host"][0]["source"] == "default"
        assert sources["host"][1]["source"] == "env_override"


# =====================================================================
# Public API (module-level)
# =====================================================================


class TestPublicAPI:
    def test_get_app_settings_returns_data(self, monkeypatch: MonkeyPatch) -> None:
        # Clear env to get predictable defaults
        for var in ("HOST", "PORT", "ENABLE_WEB", "ENABLE_API", "ENABLE_DASHBOARD",
                     "ENABLE_TELEGRAM", "ENABLE_DISCORD"):
            monkeypatch.delenv(var, raising=False)

        # Reset singleton
        import agent.modules.settings.public as pub
        monkeypatch.setattr(pub, "_settings_service", None)

        data = pub.get_app_settings()
        assert isinstance(data, AppSettingsData)
        assert data.host == "0.0.0.0"
        assert data.port == 8000

    def test_get_settings_overview_returns_dict(self, monkeypatch: MonkeyPatch) -> None:
        import agent.modules.settings.public as pub
        monkeypatch.setattr(pub, "_settings_service", None)

        overview = pub.get_settings_overview()
        assert isinstance(overview, dict)
        assert "host" in overview

    def test_bootstrap_app_settings_delegates(self, monkeypatch: MonkeyPatch) -> None:
        """AppSettings.from_env() should delegate to settings module."""
        for var in ("HOST", "PORT", "ENABLE_WEB", "ENABLE_API", "ENABLE_DASHBOARD",
                     "ENABLE_TELEGRAM", "ENABLE_DISCORD"):
            monkeypatch.delenv(var, raising=False)

        import agent.modules.settings.public as pub
        monkeypatch.setattr(pub, "_settings_service", None)

        from agent.bootstrap.settings import AppSettings
        settings = AppSettings.from_env()

        assert settings.host == "0.0.0.0"
        assert settings.port == 8000
        assert settings.enable_web is True

    def test_env_override_propagates_through_bootstrap(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("PORT", "9999")
        monkeypatch.setenv("ENABLE_API", "false")

        import agent.modules.settings.public as pub
        monkeypatch.setattr(pub, "_settings_service", None)

        from agent.bootstrap.settings import AppSettings
        settings = AppSettings.from_env()

        assert settings.port == 9999
        assert settings.enable_api is False


# =====================================================================
# Import boundary checks
# =====================================================================


class TestImportBoundary:
    def test_delivery_does_not_import_settings_infrastructure(self) -> None:
        """Dashboard router should use settings module via public API or service."""
        import importlib
        import inspect

        mod = importlib.import_module("agent.delivery.http.dashboard.router")
        source = inspect.getsource(mod)

        assert "agent.modules.settings.infrastructure" not in source
