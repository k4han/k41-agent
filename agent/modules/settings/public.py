"""Public interface for the settings module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

from agent.modules.settings.application.get_settings import (
    get_all_sources,
    get_merged_settings,
    get_settings_with_sources,
)
from agent.modules.settings.application.settings_service import SettingsService
from agent.modules.settings.application.update_settings import (
    delete_setting,
    update_channel_enabled,
    update_setting,
)
from agent.modules.settings.domain.settings_value import (
    AppSettingsData,
    SettingsSource,
    SettingsValue,
)
from agent.modules.settings.infrastructure.config_file_repository import (
    ConfigFileRepository,
)
from agent.modules.settings.infrastructure.default_repository import (
    DefaultSettingsRepository,
)
from agent.modules.settings.infrastructure.env_repository import EnvSettingsRepository
from agent.modules.settings.infrastructure.models import UserPreferences

# --- Module-level singleton ---

_settings_service: SettingsService | None = None


def _get_settings_service() -> SettingsService:
    global _settings_service
    if _settings_service is None:
        _settings_service = SettingsService(
            repositories=[
                DefaultSettingsRepository(),
                ConfigFileRepository(),
                # DB repository could be added here when async init is done
                EnvSettingsRepository(),
            ]
        )
    return _settings_service


def get_app_settings() -> AppSettingsData:
    """Return the fully merged application settings."""
    return get_merged_settings(_get_settings_service())


def get_settings_overview() -> dict[str, dict[str, object]]:
    """Return effective settings with their sources — for the dashboard."""
    return get_settings_with_sources(_get_settings_service())


def get_settings_sources_detail() -> dict[str, list[dict[str, object]]]:
    """Return all values from all sources, grouped by key."""
    return get_all_sources(_get_settings_service())


def get_service() -> SettingsService:
    """Return the singleton SettingsService instance."""
    return _get_settings_service()


__all__ = [
    "AppSettingsData",
    "SettingsService",
    "SettingsSource",
    "SettingsValue",
    "delete_setting",
    "get_app_settings",
    "get_service",
    "get_settings_overview",
    "get_settings_sources_detail",
    "update_channel_enabled",
    "update_setting",
    "UserPreferences",
]
