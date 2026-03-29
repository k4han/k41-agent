"""Port definitions (interfaces) for the settings module."""

from __future__ import annotations

from typing import Protocol

from agent.modules.settings.domain.settings_value import SettingsValue


class SettingsRepository(Protocol):
    """Read-only source of settings values."""

    def get_all(self) -> dict[str, SettingsValue]: ...

    def get(self, key: str) -> SettingsValue | None: ...


class SettingsWriter(Protocol):
    """Persist a desired-state setting change."""

    async def save(self, key: str, value: str | None) -> None: ...

    async def delete(self, key: str) -> bool: ...


__all__ = ["SettingsRepository", "SettingsWriter"]
