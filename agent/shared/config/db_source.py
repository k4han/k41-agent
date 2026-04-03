from __future__ import annotations

from typing import Any

# Placeholder for future database config source


class DatabaseConfigSource:
    """Read system-wide configuration from database.

    NOTE: This is a placeholder for future implementation.
    This will load config from a system_config table (not user_preferences).

    User preferences use the existing user_preferences table.
    System config is for application-wide settings that can be edited via UI.
    """

    def __init__(self) -> None:
        self._priority = 200  # Higher than YAML
        self._cache: dict[str, Any] | None = None

    def get(self, key: str) -> Any | None:
        """Get config value from database.

        TODO: Implement database query
        """
        return None

    def get_all(self) -> dict[str, Any]:
        """Get all config values from database.

        TODO: Implement database query
        """
        return {}

    def reload(self) -> None:
        """Clear cache and reload from database."""
        self._cache = None

    @property
    def priority(self) -> int:
        """Return priority (200 = highest)."""
        return self._priority


__all__ = ["DatabaseConfigSource"]
