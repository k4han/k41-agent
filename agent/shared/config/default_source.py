from __future__ import annotations

from typing import Any


class DefaultConfigSource:
    """Hardcoded default configuration values.

    This source provides sensible defaults for all configuration keys.
    It has the lowest priority and is overridden by all other sources.
    """

    def __init__(self) -> None:
        self._priority = 0  # Lowest priority
        self._defaults: dict[str, Any] = {
            # Server configuration
            "host": "0.0.0.0",
            "port": 8000,
            "enable_web": True,
            "enable_api": True,
            "enable_dashboard": True,
            # Database: Empty by default, will use SQLite if not set
            # Users only need to set this for PostgreSQL
            "database.url": "",
            # LLM provider configuration
            "llm.base_url": "https://api.mistral.ai/v1",
            "llm.model": "devstral-2512",
            "llm.temperature": 0.0,
            # Channel integrations
            "channels.telegram.enabled": True,
            "channels.discord.enabled": True,
            # Security
            "persistence.allow_any_path": False,
        }

    def get(self, key: str) -> Any | None:
        """Get a default config value by key."""
        return self._defaults.get(key)

    def get_all(self) -> dict[str, Any]:
        """Get all default config values."""
        return dict(self._defaults)

    def reload(self) -> None:
        """No-op for defaults (they never change)."""
        pass

    @property
    def priority(self) -> int:
        """Return priority (0 = lowest)."""
        return self._priority


__all__ = ["DefaultConfigSource"]
