"""Domain models for configuration system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent.shared.config.constants import DEFAULT_CONFIG, KNOWN_RUNTIME_KEYS, get_channel_enabled_key


class SettingsSource(str, Enum):
    """Where a settings value originates from.

    Listed in ascending precedence order — later sources override earlier ones.
    """

    DEFAULT = "default"
    CONFIG_FILE = "config_file"
    DATABASE = "database"


@dataclass(frozen=True, slots=True)
class SettingsValue:
    """A single settings entry with its resolved value and origin."""

    key: str
    value: object
    source: SettingsSource

    def to_dict(self) -> dict[str, object]:
        """Serialize to dashboard-friendly dict format."""
        return {
            "value": self.value,
            "source": self.source.value,
        }


def _default_channel_enabled() -> dict[str, bool]:
    """Derive default channel_enabled from DEFAULT_CONFIG."""
    result: dict[str, bool] = {}
    for key in KNOWN_RUNTIME_KEYS:
        if key.startswith("channels.") and key.endswith(".enabled"):
            parts = key.split(".")
            if len(parts) == 3:
                channel_name = parts[1]
                result[channel_name] = DEFAULT_CONFIG.get(get_channel_enabled_key(channel_name), True)
    return result


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Consolidated runtime settings produced by merging all sources."""

    channel_enabled: dict[str, bool] = field(default_factory=_default_channel_enabled)


def build_settings_values(
    data: dict[str, object],
    source: SettingsSource,
    keys: set[str] | None = None,
) -> dict[str, SettingsValue]:
    """Build SettingsValue objects from raw data dict.

    Args:
        data: Raw config data
        source: Source type for all values
        keys: Optional set of keys to filter. If None, returns all keys.

    Returns:
        Dictionary mapping keys to SettingsValue objects
    """
    items = data.items() if keys is None else ((k, v) for k, v in data.items() if k in keys)
    return {
        key: SettingsValue(key=key, value=val, source=source)
        for key, val in items
    }


__all__ = [
    "RuntimeSettings",
    "SettingsSource",
    "SettingsValue",
    "build_settings_values",
]
