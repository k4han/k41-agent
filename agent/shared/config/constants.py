"""Configuration constants and known keys."""

from __future__ import annotations

import re
from typing import Any


# Runtime configuration key patterns
# These patterns define which keys can be updated at runtime
RUNTIME_KEY_PATTERNS = [
    r"^channels\.(telegram|discord)\.(enabled|bot_token|default_agent|code_agent|research_agent)$",
    r"^llm\.(provider|default_provider|api_key|base_url|model|default_model|temperature)$",
    r"^llm\.providers\.[A-Za-z0-9_-]+\.(provider|type|api_key|base_url|model|default_model|temperature|enabled)$",
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
    for prop in (
        "provider",
        "default_provider",
        "api_key",
        "base_url",
        "model",
        "default_model",
        "temperature",
    ):
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
    "llm.provider": "openai_compatible",
    "llm.default_provider": "",
    "llm.base_url": "",
    "llm.model": "",
    "llm.default_model": "",
    "llm.temperature": 0.0,
    # Channel integrations
    "channels.telegram.enabled": True,
    "channels.discord.enabled": True,
    # Security
    "persistence.allow_any_path": False,
    "security.jwt_secret": "",
}


# Metadata for settings - used by dashboard to render appropriate input types
SETTING_METADATA: dict[str, dict[str, Any]] = {
    # Channel settings
    "channels.telegram.enabled": {
        "type": "boolean",
        "description": "Enable Telegram channel integration",
        "category": "channels",
        "label": "Telegram Enabled",
    },
    "channels.telegram.bot_token": {
        "type": "password",
        "description": "Telegram bot token from @BotFather",
        "category": "channels",
        "label": "Telegram Bot Token",
    },
    "channels.telegram.default_agent": {
        "type": "text",
        "description": "Default agent for Telegram DM",
        "category": "channels",
        "label": "Telegram Default Agent",
    },
    "channels.telegram.code_agent": {
        "type": "text",
        "description": "Agent triggered by /code command",
        "category": "channels",
        "label": "Telegram Code Agent",
    },
    "channels.telegram.research_agent": {
        "type": "text",
        "description": "Agent triggered by /research command",
        "category": "channels",
        "label": "Telegram Research Agent",
    },
    "channels.discord.enabled": {
        "type": "boolean",
        "description": "Enable Discord channel integration",
        "category": "channels",
        "label": "Discord Enabled",
    },
    "channels.discord.bot_token": {
        "type": "password",
        "description": "Discord bot token from Developer Portal",
        "category": "channels",
        "label": "Discord Bot Token",
    },
    "channels.discord.default_agent": {
        "type": "text",
        "description": "Default agent for Discord DM",
        "category": "channels",
        "label": "Discord Default Agent",
    },
    "channels.discord.code_agent": {
        "type": "text",
        "description": "Agent triggered by /code command",
        "category": "channels",
        "label": "Discord Code Agent",
    },
    "channels.discord.research_agent": {
        "type": "text",
        "description": "Agent triggered by /research command",
        "category": "channels",
        "label": "Discord Research Agent",
    },
    # LLM settings
    "llm.provider": {
        "type": "text",
        "description": "Legacy provider selector; use llm.default_provider for multi-provider setup",
        "category": "llm",
        "label": "LLM Provider",
    },
    "llm.default_provider": {
        "type": "text",
        "description": "Default provider name (or provider type alias) used at runtime",
        "category": "llm",
        "label": "LLM Default Provider",
    },
    "llm.api_key": {
        "type": "password",
        "description": "API key for LLM provider",
        "category": "llm",
        "label": "LLM API Key",
    },
    "llm.base_url": {
        "type": "url",
        "description": "Base URL for LLM API (e.g., https://api.example.com/v1)",
        "category": "llm",
        "label": "LLM Base URL",
    },
    "llm.model": {
        "type": "text",
        "description": "Legacy global model override (optional)",
        "category": "llm",
        "label": "LLM Model",
    },
    "llm.default_model": {
        "type": "text",
        "description": "Global default model override used when provider model is not set",
        "category": "llm",
        "label": "LLM Default Model",
    },
    "llm.temperature": {
        "type": "number",
        "description": "LLM temperature (0.0 = deterministic, 2.0 = creative)",
        "category": "llm",
        "label": "LLM Temperature",
        "min": 0,
        "max": 2,
        "step": 0.1,
    },
    # Database settings
    "database.url": {
        "type": "url",
        "description": "Database connection URL (e.g., postgresql+asyncpg://user:password@host:5432/dbname)",
        "category": "database",
        "label": "Database URL",
    },
    # Security settings
    "security.jwt_secret": {
        "type": "password",
        "description": "Secret key for JWT token signing",
        "category": "security",
        "label": "JWT Secret",
    },
}


# Default metadata for unknown keys - defined once to avoid GC pressure
_DEFAULT_META: dict[str, Any] = {
    "type": "text",
    "description": "",
    "category": "general",
    "label": "",
}

# Field order and metadata for provider settings.
# Order is derived from the dict to ensure consistency with _PROVIDER_SETTING_FIELD_META.
_PROVIDER_SETTING_FIELD_META: dict[str, dict[str, Any]] = {
    "provider": {
        "type": "text",
        "description": "Provider backend type (openai_compatible or google)",
        "label": "Provider Type",
    },
    "type": {
        "type": "text",
        "description": "Provider backend type alias",
        "label": "Provider Type",
    },
    "api_key": {
        "type": "password",
        "description": "API key for this provider",
        "label": "API Key",
    },
    "base_url": {
        "type": "url",
        "description": "Base URL for OpenAI-compatible provider",
        "label": "Base URL",
    },
    "model": {
        "type": "text",
        "description": "Legacy provider-level model override",
        "label": "Model",
    },
    "default_model": {
        "type": "text",
        "description": "Default model for this provider",
        "label": "Default Model",
    },
    "temperature": {
        "type": "number",
        "description": "Temperature for this provider (0.0 = deterministic, 2.0 = creative)",
        "label": "Temperature",
        "min": 0,
        "max": 2,
        "step": 0.1,
    },
    "enabled": {
        "type": "boolean",
        "description": "Enable or disable this provider",
        "label": "Enabled",
    },
}


def _split_provider_key(key: str) -> tuple[str, str] | None:
    """Parse "llm.providers.{name}.{field}" into (name, field)."""
    prefix = "llm.providers."
    if not key.startswith(prefix):
        return None
    remainder = key[len(prefix):]
    if "." not in remainder:
        return None
    provider_name, field_name = remainder.split(".", 1)
    if not provider_name:
        return None
    return provider_name, field_name


def _provider_setting_metadata(key: str) -> dict[str, Any] | None:
    parsed = _split_provider_key(key)
    if parsed is None:
        return None
    provider_name, field_name = parsed

    base = _PROVIDER_SETTING_FIELD_META.get(field_name)
    if base is None:
        return {
            **_DEFAULT_META,
            "category": "llm",
            "description": "Provider-specific setting",
            "label": f"{provider_name}: {field_name}",
        }

    metadata: dict[str, Any] = {
        "type": base["type"],
        "description": base["description"],
        "category": "llm",
        "label": f"{provider_name}: {base['label']}",
    }
    for key_name in ("min", "max", "step"):
        if key_name in base:
            metadata[key_name] = base[key_name]
    return metadata


# Derived ordering — must match keys in _PROVIDER_SETTING_FIELD_META
PROVIDER_SETTING_FIELD_ORDER: list[str] = list(_PROVIDER_SETTING_FIELD_META.keys())


def parse_provider_key(key: str) -> tuple[str, str] | None:
    """Parse a provider config key into (provider_name, field_name).

    Examples:
        "llm.providers.foo.api_key" -> ("foo", "api_key")
        "llm.providers.foo.enabled" -> ("foo", "enabled")

    Returns None if the key doesn't match the provider key pattern.
    """
    return _split_provider_key(key)


def get_setting_metadata(key: str) -> dict[str, Any]:
    """Get metadata for a setting key.

    Args:
        key: Config key

    Returns:
        Metadata dict with type, description, category, label
    """
    meta = SETTING_METADATA.get(key)
    if meta is None:
        provider_meta = _provider_setting_metadata(key)
        if provider_meta is not None:
            return provider_meta
        return {**_DEFAULT_META, "label": key}
    return meta


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
    "SETTING_METADATA",
    "get_setting_metadata",
    "parse_provider_key",
    "PROVIDER_SETTING_FIELD_ORDER",
]
