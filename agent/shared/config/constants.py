"""Configuration constants and known keys."""

from __future__ import annotations

import re
from typing import Any


# Runtime configuration key patterns
# These patterns define which keys can be updated at runtime
RUNTIME_KEY_PATTERNS = [
    r"^channels\.(telegram|discord)\.(enabled|bot_token|default_agent|code_agent|research_agent)$",
    r"^llm\.(api_key|base_url|model|temperature)$",
    r"^database\.url$",
    r"^security\.jwt_secret$",
]


def is_runtime_key(key: str) -> bool:
    """Check if a key is a valid runtime configuration key."""
    return any(re.match(pattern, key) for pattern in RUNTIME_KEY_PATTERNS)


# Expand patterns into valid runtime keys for iteration
def _expand_runtime_keys() -> set[str]:
    """Expand patterns into a set of all valid runtime keys."""
    keys: set[str] = set()
    for channel in ("telegram", "discord"):
        for prop in ("enabled", "bot_token", "default_agent", "code_agent", "research_agent"):
            keys.add(f"channels.{channel}.{prop}")
    for prop in ("api_key", "base_url", "model", "temperature"):
        keys.add(f"llm.{prop}")
    keys.add("database.url")
    keys.add("security.jwt_secret")
    return keys


KNOWN_RUNTIME_KEYS: set[str] = _expand_runtime_keys()

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
    "security.jwt_secret": "",
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
    "RUNTIME_KEY_PATTERNS",
    "is_runtime_key",
    "KNOWN_RUNTIME_KEYS",
    "get_channel_enabled_key",
]
