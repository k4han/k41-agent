"""Unified configuration service merging ConfigService and RuntimeSettingsService.

This service provides both:
1. Simple config access (get, get_str, get_int, get_bool, etc.)
2. Advanced settings tracking (get_effective, list_all_by_source, etc.)

Precedence (low → high):
    DEFAULT (priority 0) → CONFIG_FILE (priority 100)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.shared.config.constants import (
    KNOWN_RUNTIME_KEYS,
    get_channel_enabled_key,
    get_setting_metadata,
)
from agent.shared.config.models import RuntimeSettings, SettingsValue
from agent.shared.infrastructure.config_file import coerce_bool

logger = logging.getLogger(__name__)

_SETTING_META_OPTIONAL_KEYS = ("min", "max", "step")


def _build_settings_overview_entry(
    setting_value: SettingsValue,
    metadata: dict[str, Any],
) -> dict[str, object]:
    entry: dict[str, object] = {
        **setting_value.to_dict(),
        "input_type": metadata["type"],
        "description": metadata["description"],
        "category": metadata["category"],
        "label": metadata["label"],
    }

    for key in _SETTING_META_OPTIONAL_KEYS:
        if key in metadata:
            entry[key] = metadata[key]
    return entry


class ConfigService:
    """Unified configuration service with multi-source support.

    This service merges configuration from multiple sources (defaults, YAML file,
    database, etc.) with a defined precedence order. Higher priority sources
    override lower priority ones.

    Supports both simple config access and advanced settings tracking.
    """

    def __init__(self, sources: list[Any]) -> None:
        """Initialize config service with sources.

        Args:
            sources: List of ConfigSource instances
        """
        # Sort sources by priority (lowest first, so highest wins in merge)
        self._sources = sorted(sources, key=lambda s: s.priority)

    # --- Simple config API (backward compatible with old ConfigService) ---

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value with precedence resolution.

        Args:
            key: Config key in dot notation (e.g., "llm.api_key")
            default: Default value if key not found

        Returns:
            Config value from highest priority source, or default
        """
        result = default
        for source in self._sources:
            value = source.get(key)
            if value is not None:
                result = value
        return result

    def get_str(self, key: str, default: str = "") -> str:
        """Get string config value.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            String value
        """
        value = self.get(key, default)
        return str(value) if value is not None else default

    def get_int(self, key: str, default: int = 0) -> int:
        """Get integer config value.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Integer value
        """
        value = self.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("Config key '%s' is not an int: %s — using default %s", key, value, default)
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean config value.

        Supports string values: "1", "true", "yes", "on" (case-insensitive).

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Boolean value
        """
        value = self.get(key, default)
        return coerce_bool(value) if value is not None else default

    def get_dict(self, key: str, default: dict | None = None) -> dict:
        """Get dict config value.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Dict value
        """
        value = self.get(key, default or {})
        if isinstance(value, dict):
            return value
        return default or {}

    def get_path(self, key: str, default: str = "") -> Path:
        """Get path config value with ~ expansion.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Path object with ~ expanded to home directory
        """
        value = self.get_str(key, default)
        if "~" in value:
            return Path(value.replace("~", str(Path.home())))
        return Path(value)

    def get_all(self) -> dict[str, Any]:
        """Get all effective config values (merged from all sources).

        Returns:
            Dictionary of all config key-value pairs
        """
        result: dict[str, Any] = {}
        for source in self._sources:
            result.update(source.get_all())
        return result

    def reload(self) -> None:
        """Reload all sources (clears caches)."""
        for source in self._sources:
            source.reload()

    # --- Advanced settings API (from RuntimeSettingsService) ---

    def get_effective(self, key: str) -> SettingsValue | None:
        """Return the effective value for *key* after precedence merge.

        This tracks which source the value came from.
        """
        result: SettingsValue | None = None
        for source in self._sources:
            val = source.get_settings_value(key)
            if val is not None:
                result = val
        return result

    def list_all(self) -> dict[str, SettingsValue]:
        """Return all effective settings keyed by canonical name.

        Only returns known runtime keys with source tracking.
        """
        merged: dict[str, SettingsValue] = {}
        for key in KNOWN_RUNTIME_KEYS:
            for source in self._sources:
                val = source.get_settings_value(key)
                if val is not None:
                    merged[key] = val

        return merged

    def list_all_by_source(self) -> dict[str, list[SettingsValue]]:
        """Return every value from every source, grouped by key.

        Useful for the dashboard to show where each value comes from.
        """
        by_key: dict[str, list[SettingsValue]] = {}
        for key in KNOWN_RUNTIME_KEYS:
            for source in self._sources:
                val = source.get_settings_value(key)
                if val is not None:
                    by_key.setdefault(key, []).append(val)
        return by_key

    def get_runtime_settings(self) -> RuntimeSettings:
        """Build a consolidated ``RuntimeSettings`` from merged sources."""
        merged = self.list_all()
        defaults = RuntimeSettings()
        channel_enabled = dict(defaults.channel_enabled)
        for channel_name in channel_enabled:
            canon = get_channel_enabled_key(channel_name)
            sv = merged.get(canon)
            if sv is not None:
                channel_enabled[channel_name] = coerce_bool(sv.value)
        return RuntimeSettings(channel_enabled=channel_enabled)

    def get_settings_overview(self) -> dict[str, dict[str, object]]:
        """Dashboard-friendly overview: effective value + source + metadata for each key."""
        merged = self.list_all()
        return {
            key: _build_settings_overview_entry(sv, get_setting_metadata(key))
            for key, sv in sorted(merged.items())
        }

    def get_settings_sources(self) -> dict[str, list[dict[str, object]]]:
        """Dashboard-friendly: all sources for each key."""
        by_key = self.list_all_by_source()
        return {
            key: [sv.to_dict() for sv in vals]
            for key, vals in sorted(by_key.items())
        }

    def update_setting(self, key: str, value: Any) -> None:
        """Update a specific config setting.

        Args:
            key: Config key to update
            value: New value to set
        """
        for source in self._sources:
            if hasattr(source, "update_setting"):
                source.update_setting(key, value)
        self.reload()


# Singleton instances
_config_service: ConfigService | None = None
_config_sources: list | None = None


def get_config_service() -> ConfigService:
    """Get or create the global config service.

    Returns:
        Singleton ConfigService instance
    """
    global _config_service, _config_sources
    if _config_service is None:
        from agent.shared.config.default_source import DefaultConfigSource
        from agent.shared.config.yaml_source import YamlConfigSource

        _config_sources = [
            DefaultConfigSource(),
            YamlConfigSource(),
        ]
        _config_service = ConfigService(_config_sources)
    return _config_service


def reload_config() -> None:
    """Reload configuration from all sources."""
    service = get_config_service()
    service.reload()


__all__ = ["ConfigService", "get_config_service", "reload_config"]
