"""Configuration constants and known keys."""

from __future__ import annotations

from typing import Any


# Known runtime configuration keys
# These keys are validated and tracked for source precedence
KNOWN_RUNTIME_KEYS: set[str] = {
    # Channel settings
    "channels.telegram.enabled",
    "channels.discord.enabled",
}

# Default configuration values
DEFAULT_CONFIG: dict[str, Any] = {
    # Server configuration
    "host": "0.0.0.0",
    "port": 8000,
    "enable_web": True,
    "enable_api": True,
    "enable_dashboard": True,
    # Database: Empty by default, will use SQLite if not set
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


def get_channel_enabled_key(channel_name: str) -> str:
    """Build the config key for a channel's enabled setting.

    Args:
        channel_name: Name of the channel (e.g., "telegram", "discord")

    Returns:
        Config key in format "channels.{channel_name}.enabled"
    """
    return f"channels.{channel_name}.enabled"


__all__ = [
    "DEFAULT_CONFIG",
    "KNOWN_RUNTIME_KEYS",
    "get_channel_enabled_key",
]
