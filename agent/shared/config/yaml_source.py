from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.shared.config.constants import DEFAULT_CONFIG, is_database_runtime_key
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


def _default_file_values() -> dict[str, Any]:
    return {
        key: value
        for key, value in DEFAULT_CONFIG.items()
        if not is_database_runtime_key(key)
    }


def _delete_nested_key(data: dict[str, Any], key: str) -> bool:
    parts = key.split(".")
    current: Any = data
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        parents.append((current, part))
        current = current[part]

    if not isinstance(current, dict) or parts[-1] not in current:
        return False

    del current[parts[-1]]

    for parent, part in reversed(parents):
        child = parent.get(part)
        if isinstance(child, dict) and not child:
            del parent[part]
            continue
        break
    return True


class YamlConfigSource:
    """Read configuration from YAML file.

    This source loads configuration from a YAML file and flattens nested
    structures into dot-notation keys. Database-owned runtime keys are ignored.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CONFIG_PATH
        self._cache: dict[str, Any] | None = None
        self._cache_mtime_ns: int | None = None
        self._priority = 100  # Middle priority

    def get(self, key: str) -> Any | None:
        """Get a config value from YAML file."""
        if is_database_runtime_key(key):
            return None
        data = self._load()
        return data.get(key)

    def get_all(self) -> dict[str, Any]:
        """Get all config values from YAML file."""
        return {
            key: value
            for key, value in self._load().items()
            if not is_database_runtime_key(key)
        }

    def get_settings_value(self, key: str) -> SettingsValue | None:
        """Get a config value as SettingsValue."""
        if is_database_runtime_key(key):
            return None
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
        data = self.get_all()
        return build_settings_values(data, SettingsSource.CONFIG_FILE, keys)

    def update_setting(self, key: str, value: Any) -> None:
        self.update_settings({key: value})

    def can_update_key(self, key: str) -> bool:
        return not is_database_runtime_key(key)

    def ensure_default_file(self) -> bool:
        """Create or backfill config.yaml with missing YAML-owned defaults."""
        defaults = _default_file_values()
        if not self._path.exists():
            self._write_flat(defaults)
            return True

        current = self.get_all()
        missing_defaults = {
            key: value
            for key, value in defaults.items()
            if key not in current
        }
        if not missing_defaults:
            return False

        data = self._read_nested()
        merge_nested_dicts(data, unflatten_config_mapping(missing_defaults))
        self._write_nested(data)
        return True

    def update_settings(self, updates: dict[str, Any]) -> None:
        writable_updates = {
            key: value
            for key, value in updates.items()
            if self.can_update_key(key)
        }
        if not writable_updates:
            return

        data = (
            self._read_nested()
            if self._path.exists()
            else unflatten_config_mapping(_default_file_values())
        )

        nested_update = unflatten_config_mapping(writable_updates)
        merge_nested_dicts(data, nested_update)

        self._write_nested(data)

    def delete_setting_tree(self, key: str) -> bool:
        if not self._path.exists():
            return False

        data = self._read_nested()

        deleted = _delete_nested_key(data, key)
        if not deleted:
            return False

        self._write_nested(data)
        return True

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

    def _read_nested(self) -> dict[str, Any]:
        import yaml

        try:
            raw = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_flat(self, data: dict[str, Any]) -> None:
        self._write_nested(unflatten_config_mapping(data))

    def _write_nested(self, data: dict[str, Any]) -> None:
        import yaml

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.reload()


__all__ = ["DEFAULT_CONFIG_PATH", "YamlConfigSource"]
