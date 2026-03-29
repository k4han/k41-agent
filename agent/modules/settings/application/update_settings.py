"""Use cases for writing / updating settings (desired state)."""

from __future__ import annotations

from agent.modules.settings.domain.ports import SettingsWriter


async def update_setting(writer: SettingsWriter, key: str, value: str | None) -> None:
    """Persist a desired-state setting change."""
    await writer.save(key, value)


async def delete_setting(writer: SettingsWriter, key: str) -> bool:
    """Remove a persisted desired-state setting."""
    return await writer.delete(key)


async def update_channel_enabled(
    writer: SettingsWriter,
    channel_name: str,
    enabled: bool,
) -> None:
    """Persist the desired enabled state for a channel."""
    key = f"channels.{channel_name}.enabled"
    await writer.save(key, str(enabled).lower())


__all__ = ["delete_setting", "update_channel_enabled", "update_setting"]
