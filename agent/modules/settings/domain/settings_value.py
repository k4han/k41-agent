"""Domain entities for the settings module."""

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
class AppSettingsData:
    """Consolidated application settings produced by merging all sources."""

    host: str = "0.0.0.0"
    port: int = 8000
    enable_web: bool = True
    enable_api: bool = True
    enable_dashboard: bool = True
    service_boot_flags: dict[str, bool] = field(default_factory=lambda: {
        "telegram": True,
        "discord": True,
    })


# --- key constants ---------------------------------------------------

KNOWN_KEYS: set[str] = {
    "host",
    "port",
    "enable_web",
    "enable_api",
    "enable_dashboard",
    "channels.telegram.enabled",
    "channels.discord.enabled",
}

__all__ = [
    "AppSettingsData",
    "KNOWN_KEYS",
    "SettingsSource",
    "SettingsValue",
]
