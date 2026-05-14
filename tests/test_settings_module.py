"""Tests for the runtime settings module."""

from __future__ import annotations

import textwrap
from pathlib import Path

from agent.shared.config import (
    KNOWN_RUNTIME_KEYS,
    RuntimeSettings,
    SettingsSource,
    SettingsValue,
    ConfigService,
)
from agent.shared.config.constants import get_setting_metadata, is_runtime_key
from agent.shared.config.default_source import DefaultConfigSource


# =====================================================================
# Helpers
# =====================================================================


class StubSource:
    """In-memory config source for testing."""

    def __init__(self, entries: dict[str, SettingsValue], priority: int = 100) -> None:
        self._entries = entries
        self._priority = priority

    def get(self, key: str) -> object | None:
        sv = self._entries.get(key)
        return sv.value if sv else None

    def get_all(self) -> dict[str, object]:
        return {k: v.value for k, v in self._entries.items()}

    def get_settings_value(self, key: str) -> SettingsValue | None:
        return self._entries.get(key)

    def get_all_settings_values(self, keys: set[str] | None = None) -> dict[str, SettingsValue]:
        if keys is None:
            return dict(self._entries)
        return {k: v for k, v in self._entries.items() if k in keys}

    def reload(self) -> None:
        pass

    @property
    def priority(self) -> int:
        return self._priority


def _sv(key: str, value: object, source: SettingsSource) -> SettingsValue:
    return SettingsValue(key=key, value=value, source=source)


# =====================================================================
# DefaultConfigSource
# =====================================================================


class TestDefaultConfigSource:
    def test_returns_all_known_keys(self) -> None:
        source = DefaultConfigSource()
        all_settings = source.get_all_settings_values()

        # Check that runtime keys with defaults are in all_settings
        for key in (
            "channels.telegram.enabled",
            "channels.discord.enabled",
            "database.url",
            "llm.default_provider",
        ):
            assert key in all_settings, f"{key} should be in all_settings"

    def test_all_values_have_default_source(self) -> None:
        source = DefaultConfigSource()
        for sv in source.get_all_settings_values().values():
            assert sv.source == SettingsSource.DEFAULT

    def test_get_existing_key(self) -> None:
        source = DefaultConfigSource()
        val = source.get_settings_value("channels.telegram.enabled")
        assert val is not None
        assert val.value is True

    def test_get_missing_key(self) -> None:
        source = DefaultConfigSource()
        assert source.get_settings_value("nonexistent") is None


# =====================================================================
# YamlConfigSource
# =====================================================================


class TestYamlConfigSource:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        from agent.shared.config.yaml_source import YamlConfigSource

        source = YamlConfigSource(path=tmp_path / "nonexistent.yml")
        assert source.get_all() == {}

    def test_reads_flat_values(self, tmp_path: Path) -> None:
        from agent.shared.config.yaml_source import YamlConfigSource

        cfg = tmp_path / "config.yml"
        cfg.write_text(
            textwrap.dedent("""\
            host: 127.0.0.1
            port: 3000
            """),
            encoding="utf-8",
        )

        source = YamlConfigSource(path=cfg)
        all_settings = source.get_all_settings_values()

        assert all_settings["host"].value == "127.0.0.1"
        assert all_settings["port"].value == 3000

    def test_reads_nested_values(self, tmp_path: Path) -> None:
        from agent.shared.config.yaml_source import YamlConfigSource

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

        source = YamlConfigSource(path=cfg)
        all_settings = source.get_all_settings_values()

        assert all_settings["channels.telegram.enabled"].value is False
        assert all_settings["channels.discord.enabled"].value is True

    def test_reload_clears_cache(self, tmp_path: Path) -> None:
        from agent.shared.config.yaml_source import YamlConfigSource

        cfg = tmp_path / "config.yml"
        cfg.write_text("channels:\n  telegram:\n    enabled: true\n", encoding="utf-8")

        source = YamlConfigSource(path=cfg)
        assert source.get_settings_value("channels.telegram.enabled").value is True

        cfg.write_text("channels:\n  telegram:\n    enabled: false\n", encoding="utf-8")
        source.reload()
        assert source.get_settings_value("channels.telegram.enabled").value is False

    def test_includes_all_keys(self, tmp_path: Path) -> None:
        from agent.shared.config.yaml_source import YamlConfigSource

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

        source = YamlConfigSource(path=cfg)
        all_settings = source.get_all_settings_values()

        assert "host" in all_settings
        assert all_settings["channels.telegram.enabled"].value is False


# =====================================================================
# ConfigService — precedence merge
# =====================================================================


class TestConfigService:
    def test_higher_precedence_wins(self) -> None:
        low = StubSource({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                True,
                SettingsSource.DEFAULT,
            ),
        }, priority=0)
        high = StubSource({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                False,
                SettingsSource.CONFIG_FILE,
            ),
        }, priority=100)

        service = ConfigService(sources=[low, high])

        assert service.get_effective("channels.telegram.enabled").value is False
        assert service.get_effective("channels.telegram.enabled").source == SettingsSource.CONFIG_FILE

    def test_list_all_merges(self) -> None:
        low = StubSource({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                True,
                SettingsSource.DEFAULT,
            ),
        }, priority=0)
        high = StubSource({
            "channels.discord.enabled": _sv(
                "channels.discord.enabled",
                False,
                SettingsSource.CONFIG_FILE,
            ),
        }, priority=100)

        service = ConfigService(sources=[low, high])
        merged = service.list_all()

        assert merged["channels.telegram.enabled"].value is True
        assert merged["channels.discord.enabled"].value is False

    def test_get_runtime_settings_uses_merged_values(self) -> None:
        defaults = StubSource({
            "channels.telegram.enabled": _sv("channels.telegram.enabled", True, SettingsSource.DEFAULT),
            "channels.discord.enabled": _sv("channels.discord.enabled", True, SettingsSource.DEFAULT),
        }, priority=0)
        config_file = StubSource({
            "channels.discord.enabled": _sv("channels.discord.enabled", False, SettingsSource.CONFIG_FILE),
        }, priority=100)

        service = ConfigService(sources=[defaults, config_file])
        runtime_settings = service.get_runtime_settings()

        assert runtime_settings == RuntimeSettings(
            channel_enabled={
                "telegram": True,
                "discord": False,
            }
        )

    def test_get_effective_missing_key_returns_none(self) -> None:
        service = ConfigService(sources=[])
        assert service.get_effective("nonexistent") is None

    def test_get_settings_overview_format(self) -> None:
        source = StubSource({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                False,
                SettingsSource.CONFIG_FILE,
            ),
        }, priority=100)
        service = ConfigService(sources=[source])
        overview = service.get_settings_overview()

        assert "channels.telegram.enabled" in overview
        assert overview["channels.telegram.enabled"]["value"] is False
        assert overview["channels.telegram.enabled"]["source"] == "config_file"

    def test_get_settings_sources_format(self) -> None:
        low = StubSource({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                True,
                SettingsSource.DEFAULT,
            ),
        }, priority=0)
        high = StubSource({
            "channels.telegram.enabled": _sv(
                "channels.telegram.enabled",
                False,
                SettingsSource.CONFIG_FILE,
            ),
        }, priority=100)
        service = ConfigService(sources=[low, high])
        sources = service.get_settings_sources()

        assert "channels.telegram.enabled" in sources
        assert len(sources["channels.telegram.enabled"]) == 2
        assert sources["channels.telegram.enabled"][0]["source"] == "default"
        assert sources["channels.telegram.enabled"][1]["source"] == "config_file"

    def test_list_all_includes_dynamic_provider_runtime_keys(self) -> None:
        source = StubSource({
            "llm.providers.openai-main.api_key": _sv(
                "llm.providers.openai-main.api_key",
                "provider-key",
                SettingsSource.CONFIG_FILE,
            ),
            "host": _sv("host", "127.0.0.1", SettingsSource.CONFIG_FILE),
        }, priority=100)

        service = ConfigService(sources=[source])
        merged = service.list_all()

        assert "llm.providers.openai-main.api_key" in merged
        assert "host" not in merged


class TestRuntimeKeyMetadata:
    def test_runtime_key_allows_provider_entries(self) -> None:
        assert is_runtime_key("llm.providers.openai-main.api_key")
        assert is_runtime_key("llm.providers.openai-main.default_model")
        assert is_runtime_key("llm.providers.openai-main.models")
        assert is_runtime_key("llm.providers.openai-main.temperature")
        assert not is_runtime_key("llm.providers.openai-main.random_field")

    def test_provider_setting_metadata(self) -> None:
        meta = get_setting_metadata("llm.providers.openai-main.api_key")

        assert meta["type"] == "password"
        assert meta["category"] == "llm"
        assert "openai-main" in meta["label"]

        temperature_meta = get_setting_metadata("llm.providers.openai-main.temperature")
        assert temperature_meta["type"] == "number"
        assert temperature_meta["min"] == 0
        assert temperature_meta["max"] == 2

        models_meta = get_setting_metadata("llm.providers.openai-main.models")
        assert models_meta["type"] == "text"
        assert "models" in models_meta["label"].lower()
        assert "saved as a YAML list" in models_meta["description"]


# =====================================================================
# Public API
# =====================================================================


class TestPublicAPI:
    def test_get_config_service_returns_config_service(
        self,
        monkeypatch,
    ) -> None:
        from agent.shared.config import get_config_service
        from agent.shared.config.yaml_source import YamlConfigSource
        import agent.shared.config.yaml_source as yaml_src

        monkeypatch.setattr(
            yaml_src,
            "DEFAULT_CONFIG_PATH",
            Path("__missing_runtime_settings_config__.yml"),
        )

        service = get_config_service()
        runtime_settings = service.get_runtime_settings()

        assert isinstance(service, ConfigService)
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
        """Dashboard router should use config via shared.config."""
        import importlib
        import inspect

        mod = importlib.import_module("agent.delivery.http.dashboard.router")
        source = inspect.getsource(mod)

        assert "agent.modules.settings" not in source

    def test_bootstrap_settings_does_not_import_runtime_settings_module(self) -> None:
        import importlib
        import inspect

        bootstrap_settings = importlib.import_module("agent.bootstrap.settings")

        source = inspect.getsource(bootstrap_settings)

        assert "agent.modules.settings" not in source
