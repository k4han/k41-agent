from __future__ import annotations

from typing import Any

from agent.shared.config.constants import DEFAULT_CONFIG
from agent.shared.config.models import SettingsSource, SettingsValue, build_settings_values


class DefaultConfigSource:
    """Hardcoded default configuration values.

    This source provides sensible defaults for all configuration keys.
    It has the lowest priority and is overridden by all other sources.
    """

    def __init__(self) -> None:
        self._priority = 0  # Lowest priority
        self._defaults: dict[str, Any] = dict(DEFAULT_CONFIG)

    def get(self, key: str) -> Any | None:
        """Get a default config value by key."""
        return self._defaults.get(key)

    def get_all(self) -> dict[str, Any]:
        """Get all default config values."""
        return self._defaults

    def get_settings_value(self, key: str) -> SettingsValue | None:
        """Get a default config value as SettingsValue."""
        val = self._defaults.get(key)
        if val is None:
            return None
        return SettingsValue(key=key, value=val, source=SettingsSource.DEFAULT)

    def get_all_settings_values(self, keys: set[str] | None = None) -> dict[str, SettingsValue]:
        """Get all default config values as SettingsValue objects.

        Args:
            keys: Optional set of keys to filter. If None, returns all keys.
        """
        return build_settings_values(self._defaults, SettingsSource.DEFAULT, keys)

    def reload(self) -> None:
        """No-op for defaults (they never change)."""
        pass

    @property
    def priority(self) -> int:
        """Return priority (0 = lowest)."""
        return self._priority


__all__ = ["DefaultConfigSource"]
