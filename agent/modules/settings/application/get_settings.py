"""Use cases for reading runtime settings."""

from __future__ import annotations

from agent.modules.settings.application.settings_service import RuntimeSettingsService
from agent.modules.settings.domain.settings_value import RuntimeSettings


def get_runtime_settings(service: RuntimeSettingsService) -> RuntimeSettings:
    """Return the fully merged runtime settings."""
    return service.get_runtime_settings()


def get_settings_with_sources(service: RuntimeSettingsService) -> dict[str, dict[str, object]]:
    """Return each effective setting with its source — for the dashboard."""
    return service.get_settings_overview()


def get_all_sources(service: RuntimeSettingsService) -> dict[str, list[dict[str, object]]]:
    """Return all values from all sources, grouped by key."""
    return service.get_settings_sources()


__all__ = ["get_all_sources", "get_runtime_settings", "get_settings_with_sources"]
