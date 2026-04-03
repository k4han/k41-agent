"""Public interface for the settings module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

from agent.modules.settings.domain.settings_value import (
    KNOWN_RUNTIME_KEYS,
    RuntimeSettings,
    SettingsSource,
    SettingsValue,
)
from agent.modules.settings.application.settings_service import RuntimeSettingsService
from agent.modules.settings.infrastructure.config_file_repository import (
    ConfigFileRepository,
)
from agent.modules.settings.infrastructure.default_repository import (
    DefaultSettingsRepository,
)
from agent.modules.settings.infrastructure.models import UserPreferences

def create_runtime_settings_service() -> RuntimeSettingsService:
    """Create a runtime settings service with the default repositories."""
    return RuntimeSettingsService(
        repositories=[
            DefaultSettingsRepository(),
            ConfigFileRepository(),
        ]
    )


__all__ = [
    "KNOWN_RUNTIME_KEYS",
    "RuntimeSettings",
    "RuntimeSettingsService",
    "SettingsSource",
    "SettingsValue",
    "create_runtime_settings_service",
    "UserPreferences",
]
