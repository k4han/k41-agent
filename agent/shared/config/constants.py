"""Configuration constants and known keys."""

from __future__ import annotations

import re
from typing import Any


# Runtime configuration key patterns
# These patterns define which keys can be updated at runtime
RUNTIME_KEY_PATTERNS = [
    r"^channels\.telegram\.(enabled|bot_token|default_agent|code_agent|research_agent|update_mode|webhook_url|webhook_secret)$",
    r"^channels\.discord\.(enabled|bot_token|default_agent|code_agent|research_agent)$",
    r"^channels\.github\.(enabled|app_id|app_slug|private_key|private_key_path|webhook_secret|default_agent|trigger_label|mention_triggers)$",
    r"^llm\.default_provider$",
    r"^llm\.providers\.[A-Za-z0-9_-]+\.(provider|type|api_key|base_url|default_model|models|temperature|enabled)$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.(transport|command|args|url|enabled)$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.env\.[A-Za-z0-9_-]+$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.headers\.[A-Za-z0-9_-]+$",
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
    for prop in (
        "enabled",
        "bot_token",
        "default_agent",
        "code_agent",
        "research_agent",
        "update_mode",
        "webhook_url",
        "webhook_secret",
    ):
        keys.add(f"channels.telegram.{prop}")
    for prop in ("enabled", "bot_token", "default_agent", "code_agent", "research_agent"):
        keys.add(f"channels.discord.{prop}")
    for prop in (
        "enabled",
        "app_id",
        "app_slug",
        "private_key",
        "private_key_path",
        "webhook_secret",
        "default_agent",
        "trigger_label",
        "mention_triggers",
    ):
        keys.add(f"channels.github.{prop}")
    keys.add("llm.default_provider")
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
    "llm.default_provider": "",
    # Channel integrations
    "channels.telegram.enabled": True,
    "channels.telegram.update_mode": "polling",
    "channels.telegram.webhook_url": "",
    "channels.telegram.webhook_secret": "",
    "channels.discord.enabled": True,
    "channels.github.enabled": False,
    "channels.github.app_slug": "",
    "channels.github.trigger_label": "kaka-agent",
    "channels.github.mention_triggers": "@kaka-agent,/kaka",
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
    "channels.telegram.update_mode": {
        "type": "text",
        "description": "Telegram update mode: polling or webhook",
        "category": "channels",
        "label": "Telegram Update Mode",
    },
    "channels.telegram.webhook_url": {
        "type": "url",
        "description": "Public HTTPS endpoint for Telegram webhook mode",
        "category": "channels",
        "label": "Telegram Webhook URL",
    },
    "channels.telegram.webhook_secret": {
        "type": "password",
        "description": "Secret token checked against Telegram webhook requests",
        "category": "channels",
        "label": "Telegram Webhook Secret",
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
    "channels.github.enabled": {
        "type": "boolean",
        "description": "Enable GitHub App webhook automation",
        "category": "channels",
        "label": "GitHub Enabled",
    },
    "channels.github.app_id": {
        "type": "text",
        "description": "GitHub App ID used to mint installation tokens",
        "category": "channels",
        "label": "GitHub App ID",
    },
    "channels.github.app_slug": {
        "type": "text",
        "description": "GitHub App slug used to build the install URL",
        "category": "channels",
        "label": "GitHub App Slug",
    },
    "channels.github.private_key": {
        "type": "password",
        "description": "PEM private key for the GitHub App",
        "category": "channels",
        "label": "GitHub Private Key",
    },
    "channels.github.private_key_path": {
        "type": "text",
        "description": "Path to the PEM private key for the GitHub App",
        "category": "channels",
        "label": "GitHub Private Key Path",
    },
    "channels.github.webhook_secret": {
        "type": "password",
        "description": "Secret used to validate GitHub webhook signatures",
        "category": "channels",
        "label": "GitHub Webhook Secret",
    },
    "channels.github.default_agent": {
        "type": "text",
        "description": "Default agent for GitHub repository automation",
        "category": "channels",
        "label": "GitHub Default Agent",
    },
    "channels.github.trigger_label": {
        "type": "text",
        "description": "Default issue label that triggers GitHub automation",
        "category": "channels",
        "label": "GitHub Trigger Label",
    },
    "channels.github.mention_triggers": {
        "type": "text",
        "description": "Comma-separated comment triggers for GitHub automation",
        "category": "channels",
        "label": "GitHub Mention Triggers",
    },
    # LLM settings
    "llm.default_provider": {
        "type": "text",
        "description": "Default provider name used at runtime",
        "category": "llm",
        "label": "LLM Default Provider",
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
        "description": "Provider backend type (openai_compatible, google, or anthropic)",
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
    "default_model": {
        "type": "text",
        "description": "Default model for this provider",
        "label": "Default Model",
    },
    "models": {
        "type": "text",
        "description": "Selectable models for providers without model listing; saved as a YAML list",
        "label": "Models",
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


# Derived ordering - must match keys in _PROVIDER_SETTING_FIELD_META
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
