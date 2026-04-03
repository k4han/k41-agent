"""Tests for the runtime settings module."""

from __future__ import annotations

import textwrap
from pathlib import Path

from agent.modules.settings.application.settings_service import RuntimeSettingsService
from agent.modules.settings.domain.settings_value import (
    KNOWN_RUNTIME_KEYS,
    RuntimeSettings,
    SettingsSource,
    SettingsValue,
)
from agent.modules.settings.infrastructure.default_repository import (
    DefaultSettingsRepository,
)


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

        assert "channels.telegram.enabled" in all_settings
        assert "channels.discord.enabled" in all_settings
        assert set(all_settings) == KNOWN_RUNTIME_KEYS

    def test_all_values_have_default_source(self) -> None:
        repo = DefaultSettingsRepository()
        for sv in repo.get_all().values():
            assert sv.source == SettingsSource.DEFAULT

    def test_get_existing_key(self) -> None:
        repo = DefaultSettingsRepository()
        val = repo.get("channels.telegram.enabled")
        assert val is not None
        assert val.value is True

    def test_get_missing_key(self) -> None:
        repo = DefaultSettingsRepository()
        assert repo.get("nonexistent") is None


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
        cfg.write_text("channels:\n  telegram:\n    enabled: true\n", encoding="utf-8")

        repo = ConfigFileRepository(path=cfg)
        assert repo.get("channels.telegram.enabled").value is True

        cfg.write_text("channels:\n  telegram:\n    enabled: false\n", encoding="utf-8")
        repo.reload()
        assert repo.get("channels.telegram.enabled").value is False

    def test_filters_bootstrap_keys(self, tmp_path: Path) -> None:
        from agent.modules.settings.infrastructure.config_file_repository import (
            ConfigFileRepository,
        )

        cfg = tmp_path / "config.yml"
        cfg.write_text(
            textwrap.dedent("""\
            host: 127.0.0.1
            channels:
              telegram:
                enabled: false
            """),
            encoding="utf-8",
        )

        repo = ConfigFileRepository(path=cfg)
        all_settings = repo.get_all()

        assert "host" in all_settings
        assert all_settings["channels.telegram.enabled"].value is False


# =====================================================================
# RuntimeSettingsService — precedence merge
# =====================================================================


class TestRuntimeSettingsService:
    def test_higher_precedence_wins(self) -> None:
        low = StubRepository({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                True,
                SettingsSource.DEFAULT,
            ),
        })
        high = StubRepository({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                False,
                SettingsSource.ENV_OVERRIDE,
            ),
        })

        service = RuntimeSettingsService(repositories=[low, high])

        assert service.get_effective("channels.telegram.enabled").value is False
        assert service.get_effective("channels.telegram.enabled").source == SettingsSource.ENV_OVERRIDE

    def test_list_all_merges(self) -> None:
        low = StubRepository({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                True,
                SettingsSource.DEFAULT,
            ),
        })
        high = StubRepository({
            "channels.discord.enabled": _sv(
                "channels.discord.enabled",
                False,
                SettingsSource.CONFIG_FILE,
            ),
        })

        service = RuntimeSettingsService(repositories=[low, high])
        merged = service.list_all()

        assert merged["channels.telegram.enabled"].value is True
        assert merged["channels.discord.enabled"].value is False

    def test_get_runtime_settings_uses_merged_values(self) -> None:
        defaults = StubRepository({
            "channels.telegram.enabled": _sv("channels.telegram.enabled", True, SettingsSource.DEFAULT),
            "channels.discord.enabled": _sv("channels.discord.enabled", True, SettingsSource.DEFAULT),
        })
        env = StubRepository({
            "channels.discord.enabled": _sv("channels.discord.enabled", False, SettingsSource.ENV_OVERRIDE),
        })

        service = RuntimeSettingsService(repositories=[defaults, env])
        runtime_settings = service.get_runtime_settings()

        assert runtime_settings == RuntimeSettings(
            channel_enabled={
                "telegram": True,
                "discord": False,
            }
        )

    def test_get_effective_missing_key_returns_none(self) -> None:
        service = RuntimeSettingsService(repositories=[])
        assert service.get_effective("nonexistent") is None

    def test_get_settings_overview_format(self) -> None:
        repo = StubRepository({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                False,
                SettingsSource.CONFIG_FILE,
            ),
        })
        service = RuntimeSettingsService(repositories=[repo])
        overview = service.get_settings_overview()

        assert "channels.telegram.enabled" in overview
        assert overview["channels.telegram.enabled"]["value"] is False
        assert overview["channels.telegram.enabled"]["source"] == "config_file"

    def test_get_settings_sources_format(self) -> None:
        low = StubRepository({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                True,
                SettingsSource.DEFAULT,
            ),
        })
        high = StubRepository({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                False,
                SettingsSource.ENV_OVERRIDE,
            ),
        })
        service = RuntimeSettingsService(repositories=[low, high])
        sources = service.get_settings_sources()

        assert "channels.telegram.enabled" in sources
        assert len(sources["channels.telegram.enabled"]) == 2
        assert sources["channels.telegram.enabled"][0]["source"] == "default"
        assert sources["channels.telegram.enabled"][1]["source"] == "env_override"


# =====================================================================
# Public API
# =====================================================================


class TestPublicAPI:
    def test_create_runtime_settings_service_returns_runtime_service(
        self,
        monkeypatch: MonkeyPatch,
    ) -> None:
        for var in ("ENABLE_TELEGRAM", "ENABLE_DISCORD"):
            monkeypatch.delenv(var, raising=False)

        import agent.modules.settings.public as pub
        import agent.modules.settings.infrastructure.config_file_repository as cfg_repo

        monkeypatch.setattr(
            cfg_repo,
            "DEFAULT_CONFIG_PATH",
            Path("__missing_runtime_settings_config__.yml"),
        )

        service = pub.create_runtime_settings_service()
        runtime_settings = service.get_runtime_settings()

        assert isinstance(service, RuntimeSettingsService)
        assert runtime_settings == RuntimeSettings(
            channel_enabled={
                "telegram": True,
                "discord": True,
            }
        )


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

    def test_bootstrap_settings_does_not_import_runtime_settings_module(self) -> None:
        import importlib
        import inspect

        bootstrap_settings = importlib.import_module("agent.bootstrap.settings")

        source = inspect.getsource(bootstrap_settings)

        assert "agent.modules.settings" not in source
