from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.shared.config.models import SettingsSource, SettingsValue, build_settings_values
from agent.shared.infrastructure.config_file import (
    DEFAULT_CONFIG_PATH,
    flatten_config_mapping,
    load_flat_config_file,
    merge_nested_dicts,
    unflatten_config_mapping,
)

logger = logging.getLogger(__name__)

_MISSING = object()


class YamlConfigSource:
    """Read configuration from YAML file.

    This source loads configuration from a YAML file and flattens nested
    structures into dot-notation keys (e.g., llm.providers.primary.api_key).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CONFIG_PATH
        self._cache: dict[str, Any] | None = None
        self._cache_mtime_ns: int | None = None
        self._priority = 100  # Middle priority

    def get(self, key: str) -> Any | None:
        """Get a config value from YAML file."""
        data = self._load()
        return data.get(key)

    def get_all(self) -> dict[str, Any]:
        """Get all config values from YAML file."""
        return self._load()

    def get_settings_value(self, key: str) -> SettingsValue | None:
        """Get a config value as SettingsValue."""
        data = self._load()
        val = data.get(key, _MISSING)
        if val is _MISSING:
            return None
        return SettingsValue(key=key, value=val, source=SettingsSource.CONFIG_FILE)

    def get_all_settings_values(
        self, keys: set[str] | None = None
    ) -> dict[str, SettingsValue]:
        """Get all config values as SettingsValue objects.

        Args:
            keys: Optional set of keys to filter. If None, returns all keys.
        """
        data = self._load()
        return build_settings_values(data, SettingsSource.CONFIG_FILE, keys)

    def update_setting(self, key: str, value: Any) -> None:
        self.update_settings({key: value})

    def update_settings(self, updates: dict[str, Any]) -> None:
        import yaml

        try:
            raw = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
        except FileNotFoundError:
            data = {}

        nested_update = unflatten_config_mapping(updates)
        merge_nested_dicts(data, nested_update)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.reload()

    def reload(self) -> None:
        """Clear cache and reload from file."""
        self._cache = None
        self._cache_mtime_ns = None

    @property
    def priority(self) -> int:
        """Return priority (100 = middle)."""
        return self._priority

    def _load(self) -> dict[str, Any]:
        """Load and cache config from YAML file."""
        current_mtime_ns = self._current_mtime_ns()
        if self._cache is not None and self._cache_mtime_ns == current_mtime_ns:
            return self._cache

        self._cache = load_flat_config_file(self._path)
        self._cache_mtime_ns = self._current_mtime_ns()
        return self._cache

    def _current_mtime_ns(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return None


__all__ = ["DEFAULT_CONFIG_PATH", "YamlConfigSource"]
