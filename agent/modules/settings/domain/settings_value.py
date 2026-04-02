"""Domain entities for runtime settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SettingsSource(str, Enum):
    """Where a settings value originates from.

    Listed in ascending precedence order — later sources override earlier ones.
    """

    DEFAULT = "default"
    CONFIG_FILE = "config_file"
    DATABASE = "database"
    ENV_OVERRIDE = "env_override"


@dataclass(frozen=True, slots=True)
class SettingsValue:
    """A single settings entry with its resolved value and origin."""

    key: str
    value: object
    source: SettingsSource


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Consolidated runtime settings produced by merging all sources."""

    channel_enabled: dict[str, bool] = field(default_factory=lambda: {
        "telegram": True,
        "discord": True,
    })


# --- key constants ---------------------------------------------------

KNOWN_RUNTIME_KEYS: set[str] = {
    "channels.telegram.enabled",
    "channels.discord.enabled",
}

__all__ = [
    "KNOWN_RUNTIME_KEYS",
    "RuntimeSettings",
    "SettingsSource",
    "SettingsValue",
]
