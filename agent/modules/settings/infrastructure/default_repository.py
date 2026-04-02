"""Default runtime settings repository — hardcoded baseline values."""

from __future__ import annotations

from agent.modules.settings.domain.settings_value import (
    RuntimeSettings,
    SettingsSource,
    SettingsValue,
)


# Build defaults from RuntimeSettings defaults
_DEFAULTS = RuntimeSettings()

_DEFAULT_MAP: dict[str, object] = {
    "channels.telegram.enabled": _DEFAULTS.channel_enabled.get("telegram", True),
    "channels.discord.enabled": _DEFAULTS.channel_enabled.get("discord", True),
}


class DefaultSettingsRepository:
    """Return hardcoded default values for every known runtime key."""

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
