"""Use cases for reading settings."""

from __future__ import annotations

from agent.modules.settings.application.settings_service import SettingsService
from agent.modules.settings.domain.settings_value import AppSettingsData


def get_merged_settings(service: SettingsService) -> AppSettingsData:
    """Return the fully merged application settings."""
    return service.get_app_settings()


def get_settings_with_sources(service: SettingsService) -> dict[str, dict[str, object]]:
    """Return each effective setting with its source — for the dashboard."""
    return service.get_settings_overview()


def get_all_sources(service: SettingsService) -> dict[str, list[dict[str, object]]]:
    """Return all values from all sources, grouped by key."""
    return service.get_settings_sources()


__all__ = ["get_all_sources", "get_merged_settings", "get_settings_with_sources"]
