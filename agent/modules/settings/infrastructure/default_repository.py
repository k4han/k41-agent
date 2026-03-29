"""Default settings repository — hardcoded baseline values."""

from __future__ import annotations

from agent.modules.settings.domain.settings_value import (
    AppSettingsData,
    SettingsSource,
    SettingsValue,
)


# Build defaults from AppSettingsData defaults
_DEFAULTS = AppSettingsData()

_DEFAULT_MAP: dict[str, object] = {
    "host": _DEFAULTS.host,
    "port": _DEFAULTS.port,
    "enable_web": _DEFAULTS.enable_web,
    "enable_api": _DEFAULTS.enable_api,
    "enable_dashboard": _DEFAULTS.enable_dashboard,
    "channels.telegram.enabled": _DEFAULTS.service_boot_flags.get("telegram", True),
    "channels.discord.enabled": _DEFAULTS.service_boot_flags.get("discord", True),
}


class DefaultSettingsRepository:
    """Return hardcoded default values for every known key."""

    def get_all(self) -> dict[str, SettingsValue]:
        return {
            key: SettingsValue(key=key, value=val, source=SettingsSource.DEFAULT)
            for key, val in _DEFAULT_MAP.items()
        }

    def get(self, key: str) -> SettingsValue | None:
        val = _DEFAULT_MAP.get(key)
        if val is None:
            return None
        return SettingsValue(key=key, value=val, source=SettingsSource.DEFAULT)


__all__ = ["DefaultSettingsRepository"]
