"""Configuration constants and known keys."""

from __future__ import annotations

import re
from typing import Any

DISPLAY_TIMEZONE_CONFIG_KEY = "display.timezone"
DEFAULT_DISPLAY_TIMEZONE = "UTC"
BOOTSTRAP_CONFIG_KEYS = ("host", "port", "enable_web", "enable_api", "enable_dashboard")
BOOTSTRAP_BOOLEAN_CONFIG_KEYS = ("enable_web", "enable_api", "enable_dashboard")

LLM_FALLBACK_PROVIDER_KEY = "llm.fallback.provider"
LLM_FALLBACK_MODEL_KEY = "llm.fallback.model"
REPOSITORY_SKILLS_DIR_KEY = "skills.repository_dir"

# Runtime configuration key patterns
# These patterns define which keys can be updated at runtime
RUNTIME_KEY_PATTERNS = [
    r"^(host|port|enable_web|enable_api|enable_dashboard)$",
    r"^channels\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
    r"^channels\.telegram\.(enabled|bot_token|default_agent|code_agent|research_agent|update_mode|webhook_url|webhook_secret)$",
    r"^channels\.discord\.(enabled|bot_token|default_agent|code_agent|research_agent)$",
    r"^channels\.github\.(enabled|app_id|app_slug|private_key|private_key_path|webhook_secret|default_agent|trigger_label|mention_triggers)$",
    r"^llm\.default_model$",
    rf"^{re.escape(LLM_FALLBACK_PROVIDER_KEY)}$",
    rf"^{re.escape(LLM_FALLBACK_MODEL_KEY)}$",
    r"^llm\.providers\.[A-Za-z0-9_-]+\.(provider|type|api_key|base_url|default_model|models|temperature|enabled)$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.(transport|command|args|url|enabled)$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.env\.[A-Za-z0-9_-]+$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.headers\.[A-Za-z0-9_-]+$",
    r"^workspace\.root$",
    r"^workspace\.daytona\.(enabled|api_key|default_root|target|image|cpu|memory|disk|language|auto_stop_minutes|auto_archive_days|sweeper_interval_seconds|start_timeout_seconds|stop_timeout_seconds|sandbox_auto_stop_minutes|sandbox_auto_archive_minutes|sandbox_auto_delete_minutes|ephemeral|network_block_all|network_allow_list)$",
    r"^workspace\.modal\.(enabled|token_id|token_secret|app_name|default_root|image|sandbox_timeout_seconds|idle_timeout_seconds)$",
    r"^workspace\.openshell\.(enabled|cli_path|default_root|image|cpu|memory|create_timeout_seconds|exec_timeout_seconds|delete_timeout_seconds|list_timeout_seconds)$",
    rf"^{re.escape(REPOSITORY_SKILLS_DIR_KEY)}$",
    r"^database\.url$",
    rf"^{re.escape(DISPLAY_TIMEZONE_CONFIG_KEY)}$",
    r"^recursion_limit$",
]

DATABASE_RUNTIME_KEY_PATTERNS = [
    r"^channels\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
    r"^channels\.telegram\.(enabled|bot_token|default_agent|code_agent|research_agent|update_mode|webhook_url|webhook_secret)$",
    r"^channels\.discord\.(enabled|bot_token|default_agent|code_agent|research_agent)$",
    r"^channels\.github\.(enabled|app_id|app_slug|private_key|private_key_path|webhook_secret|default_agent|trigger_label|mention_triggers)$",
    r"^llm\.default_model$",
    rf"^{re.escape(LLM_FALLBACK_PROVIDER_KEY)}$",
    rf"^{re.escape(LLM_FALLBACK_MODEL_KEY)}$",
    r"^llm\.providers\.[A-Za-z0-9_-]+\.(provider|type|api_key|base_url|default_model|models|temperature|enabled)$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.(transport|command|args|url|enabled)$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.env\.[A-Za-z0-9_-]+$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.headers\.[A-Za-z0-9_-]+$",
    r"^workspace\.daytona\.(enabled|api_key|default_root|target|image|cpu|memory|disk|language|auto_stop_minutes|auto_archive_days|sweeper_interval_seconds|start_timeout_seconds|stop_timeout_seconds|sandbox_auto_stop_minutes|sandbox_auto_archive_minutes|sandbox_auto_delete_minutes|ephemeral|network_block_all|network_allow_list)$",
    r"^workspace\.modal\.(enabled|token_id|token_secret|app_name|default_root|image|sandbox_timeout_seconds|idle_timeout_seconds)$",
    r"^workspace\.openshell\.(enabled|cli_path|default_root|image|cpu|memory|create_timeout_seconds|exec_timeout_seconds|delete_timeout_seconds|list_timeout_seconds)$",
    rf"^{re.escape(REPOSITORY_SKILLS_DIR_KEY)}$",
    rf"^{re.escape(DISPLAY_TIMEZONE_CONFIG_KEY)}$",
    r"^recursion_limit$",
]

SENSITIVE_RUNTIME_KEY_PATTERNS = [
    r"^channels\.[A-Za-z0-9_-]+\.(bot_token|token|api_token|api_key|secret|client_secret|webhook_secret|private_key)$",
    r"^channels\.telegram\.(bot_token|webhook_secret)$",
    r"^channels\.discord\.bot_token$",
    r"^channels\.github\.(private_key|webhook_secret)$",
    r"^llm\.providers\.[A-Za-z0-9_-]+\.api_key$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.env\.[A-Za-z0-9_-]+$",
    r"^mcp\.servers\.[A-Za-z0-9_-]+\.headers\.[A-Za-z0-9_-]+$",
    r"^workspace\.daytona\.api_key$",
    r"^workspace\.modal\.(token_id|token_secret)$",
]


def is_runtime_key(key: str) -> bool:
    """Check if a key is a valid runtime configuration key."""
    return any(re.match(pattern, key) for pattern in RUNTIME_KEY_PATTERNS)


def is_database_runtime_key(key: str) -> bool:
    """Check whether a runtime key is owned by the database source."""
    return any(re.match(pattern, key) for pattern in DATABASE_RUNTIME_KEY_PATTERNS)


def is_sensitive_runtime_key(key: str) -> bool:
    """Check whether a runtime key should be encrypted at rest."""
    return any(re.match(pattern, key) for pattern in SENSITIVE_RUNTIME_KEY_PATTERNS)


# Expand patterns into valid runtime keys for iteration
def _expand_runtime_keys() -> set[str]:
    """Expand patterns into a set of all valid runtime keys."""
    keys: set[str] = set()
    keys.update(BOOTSTRAP_CONFIG_KEYS)
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
    for prop in (
        "enabled",
        "bot_token",
        "default_agent",
        "code_agent",
        "research_agent",
    ):
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
    keys.add("llm.default_model")
    keys.add(LLM_FALLBACK_PROVIDER_KEY)
    keys.add(LLM_FALLBACK_MODEL_KEY)
    keys.add("database.url")
    keys.add("workspace.root")
    keys.add("workspace.daytona.enabled")
    keys.add("workspace.daytona.api_key")
    keys.add("workspace.daytona.default_root")
    keys.add("workspace.daytona.target")
    keys.add("workspace.daytona.image")
    keys.add("workspace.daytona.cpu")
    keys.add("workspace.daytona.memory")
    keys.add("workspace.daytona.disk")
    keys.add("workspace.daytona.language")
    keys.add("workspace.daytona.auto_stop_minutes")
    keys.add("workspace.daytona.auto_archive_days")
    keys.add("workspace.daytona.sweeper_interval_seconds")
    keys.add("workspace.daytona.start_timeout_seconds")
    keys.add("workspace.daytona.stop_timeout_seconds")
    keys.add("workspace.daytona.sandbox_auto_stop_minutes")
    keys.add("workspace.daytona.sandbox_auto_archive_minutes")
    keys.add("workspace.daytona.sandbox_auto_delete_minutes")
    keys.add("workspace.daytona.ephemeral")
    keys.add("workspace.daytona.network_block_all")
    keys.add("workspace.daytona.network_allow_list")
    keys.add("workspace.modal.enabled")
    keys.add("workspace.modal.token_id")
    keys.add("workspace.modal.token_secret")
    keys.add("workspace.modal.app_name")
    keys.add("workspace.modal.default_root")
    keys.add("workspace.modal.image")
    keys.add("workspace.modal.sandbox_timeout_seconds")
    keys.add("workspace.modal.idle_timeout_seconds")
    keys.add("workspace.openshell.enabled")
    keys.add("workspace.openshell.cli_path")
    keys.add("workspace.openshell.default_root")
    keys.add("workspace.openshell.image")
    keys.add("workspace.openshell.cpu")
    keys.add("workspace.openshell.memory")
    keys.add("workspace.openshell.create_timeout_seconds")
    keys.add("workspace.openshell.exec_timeout_seconds")
    keys.add("workspace.openshell.delete_timeout_seconds")
    keys.add("workspace.openshell.list_timeout_seconds")
    keys.add(REPOSITORY_SKILLS_DIR_KEY)
    keys.add(DISPLAY_TIMEZONE_CONFIG_KEY)
    keys.add("recursion_limit")
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
    "llm.default_model": "",
    # Channel integrations
    "channels.telegram.enabled": True,
    "channels.telegram.bot_token": "",
    "channels.telegram.default_agent": "",
    "channels.telegram.code_agent": "",
    "channels.telegram.research_agent": "",
    "channels.telegram.update_mode": "polling",
    "channels.telegram.webhook_url": "",
    "channels.telegram.webhook_secret": "",
    "channels.discord.enabled": True,
    "channels.discord.bot_token": "",
    "channels.discord.default_agent": "",
    "channels.discord.code_agent": "",
    "channels.discord.research_agent": "",
    "channels.github.enabled": False,
    "channels.github.app_id": "",
    "channels.github.app_slug": "",
    "channels.github.private_key": "",
    "channels.github.private_key_path": "",
    "channels.github.webhook_secret": "",
    "channels.github.default_agent": "",
    "channels.github.trigger_label": "k41-agent",
    "channels.github.mention_triggers": "@k41-agent,/k41",
    # Security
    "persistence.allow_any_path": False,
    "workspace.root": "~/k41-agent",
    "workspace.daytona.enabled": False,
    "workspace.daytona.api_key": "",
    "workspace.daytona.default_root": "workspace",
    "workspace.daytona.target": "",
    "workspace.daytona.image": "",
    "workspace.daytona.cpu": 0,
    "workspace.daytona.memory": 0,
    "workspace.daytona.disk": 0,
    "workspace.daytona.language": "python",
    "workspace.daytona.auto_stop_minutes": 30,
    "workspace.daytona.auto_archive_days": 7,
    "workspace.daytona.sweeper_interval_seconds": 60,
    "workspace.daytona.start_timeout_seconds": 120,
    "workspace.daytona.stop_timeout_seconds": 60,
    "workspace.daytona.sandbox_auto_stop_minutes": 15,
    "workspace.daytona.sandbox_auto_archive_minutes": 10080,
    "workspace.daytona.sandbox_auto_delete_minutes": -1,
    "workspace.daytona.ephemeral": False,
    "workspace.daytona.network_block_all": False,
    "workspace.daytona.network_allow_list": "",
    "workspace.modal.enabled": False,
    "workspace.modal.token_id": "",
    "workspace.modal.token_secret": "",
    "workspace.modal.app_name": "k41-agent-sandboxes",
    "workspace.modal.default_root": "/workspace",
    "workspace.modal.image": "python:3.13-slim",
    "workspace.modal.sandbox_timeout_seconds": 3600,
    "workspace.modal.idle_timeout_seconds": 900,
    "workspace.openshell.enabled": False,
    "workspace.openshell.cli_path": "openshell",
    "workspace.openshell.default_root": "/sandbox",
    "workspace.openshell.image": "base",
    "workspace.openshell.cpu": 0,
    "workspace.openshell.memory": "",
    "workspace.openshell.create_timeout_seconds": 300,
    "workspace.openshell.exec_timeout_seconds": 120,
    "workspace.openshell.delete_timeout_seconds": 120,
    "workspace.openshell.list_timeout_seconds": 30,
    REPOSITORY_SKILLS_DIR_KEY: ".agent/skills",
    DISPLAY_TIMEZONE_CONFIG_KEY: DEFAULT_DISPLAY_TIMEZONE,
    "security.jwt_secret": "",
    "recursion_limit": 100,
}


# Metadata for settings - used by dashboard to render appropriate input types
SETTING_METADATA: dict[str, dict[str, Any]] = {
    # Bootstrap settings
    "host": {
        "type": "text",
        "description": "Server bind host. Restart required to apply changes.",
        "category": "bootstrap",
        "label": "Host",
        "restart_required": True,
    },
    "port": {
        "type": "number",
        "description": "Server bind port. Restart required to apply changes.",
        "category": "bootstrap",
        "label": "Port",
        "min": 1,
        "max": 65535,
        "step": 1,
        "restart_required": True,
    },
    "enable_web": {
        "type": "boolean",
        "description": "Enable HTTP web delivery. Restart required to apply changes.",
        "category": "bootstrap",
        "label": "Enable Web",
        "restart_required": True,
    },
    "enable_api": {
        "type": "boolean",
        "description": "Enable API delivery. Restart required to apply changes.",
        "category": "bootstrap",
        "label": "Enable API",
        "restart_required": True,
    },
    "enable_dashboard": {
        "type": "boolean",
        "description": "Enable the dashboard UI. Restart required to apply changes.",
        "category": "bootstrap",
        "label": "Enable Dashboard",
        "restart_required": True,
    },
    # Workspace settings
    "workspace.root": {
        "type": "text",
        "description": "Root directory for local workspaces (supports ~)",
        "category": "general",
        "label": "Workspace Root",
    },
    "workspace.daytona.enabled": {
        "type": "boolean",
        "description": "Enable Daytona sandbox workspaces.",
        "category": "workspace",
        "label": "Daytona Enabled",
    },
    "workspace.daytona.api_key": {
        "type": "password",
        "description": "Daytona API key used to create and attach sandbox workspaces.",
        "category": "workspace",
        "label": "Daytona API Key",
    },
    "workspace.daytona.default_root": {
        "type": "text",
        "description": "Default root directory inside Daytona sandboxes.",
        "category": "workspace",
        "label": "Daytona Default Root",
    },
    "workspace.daytona.auto_stop_minutes": {
        "type": "number",
        "description": "Stop idle Daytona sandboxes after this many minutes. Use 0 to disable.",
        "category": "workspace",
        "label": "Daytona Auto Stop Minutes",
        "min": 0,
        "step": 1,
    },
    "workspace.daytona.auto_archive_days": {
        "type": "number",
        "description": "Archive stopped Daytona sandboxes after this many idle days. Use 0 to disable.",
        "category": "workspace",
        "label": "Daytona Auto Archive Days",
        "min": 0,
        "step": 1,
    },
    "workspace.daytona.sweeper_interval_seconds": {
        "type": "number",
        "description": "Interval in seconds for the Daytona lifecycle sweeper.",
        "category": "workspace",
        "label": "Daytona Sweeper Interval Seconds",
        "min": 30,
        "step": 1,
    },
    "workspace.daytona.start_timeout_seconds": {
        "type": "number",
        "description": "Maximum seconds to wait when starting a Daytona sandbox.",
        "category": "workspace",
        "label": "Daytona Start Timeout Seconds",
        "min": 1,
        "step": 1,
    },
    "workspace.daytona.stop_timeout_seconds": {
        "type": "number",
        "description": "Maximum seconds to wait when stopping a Daytona sandbox.",
        "category": "workspace",
        "label": "Daytona Stop Timeout Seconds",
        "min": 1,
        "step": 1,
    },
    "workspace.daytona.target": {
        "type": "text",
        "description": "Target runner region for new sandboxes (e.g. us-east-1). Leave empty for default.",
        "category": "workspace",
        "label": "Daytona Target Region",
    },
    "workspace.daytona.image": {
        "type": "text",
        "description": "Docker image for new sandboxes (e.g. python:3.12-slim). Leave empty for Daytona default.",
        "category": "workspace",
        "label": "Daytona Image",
    },
    "workspace.daytona.cpu": {
        "type": "number",
        "description": "CPU cores for new sandboxes (1-4). Use 0 for Daytona default (1).",
        "category": "workspace",
        "label": "Daytona CPU Cores",
        "min": 0,
        "max": 4,
        "step": 1,
    },
    "workspace.daytona.memory": {
        "type": "number",
        "description": "Memory in GiB for new sandboxes (1-8). Use 0 for Daytona default (1).",
        "category": "workspace",
        "label": "Daytona Memory GiB",
        "min": 0,
        "max": 8,
        "step": 1,
    },
    "workspace.daytona.disk": {
        "type": "number",
        "description": "Disk space in GiB for new sandboxes (1-10). Use 0 for Daytona default (3).",
        "category": "workspace",
        "label": "Daytona Disk GiB",
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "workspace.daytona.language": {
        "type": "text",
        "description": "Runtime language for new sandboxes: python, typescript, or javascript.",
        "category": "workspace",
        "label": "Daytona Language",
    },
    "workspace.daytona.sandbox_auto_stop_minutes": {
        "type": "number",
        "description": "Auto-stop interval on Daytona platform (minutes). Use 0 to disable. Default 15.",
        "category": "workspace",
        "label": "Daytona Platform Auto-Stop",
        "min": 0,
        "step": 1,
    },
    "workspace.daytona.sandbox_auto_archive_minutes": {
        "type": "number",
        "description": "Auto-archive interval on Daytona platform (minutes). Use 0 for max (30 days). Default 7 days.",
        "category": "workspace",
        "label": "Daytona Platform Auto-Archive",
        "min": 0,
        "step": 1,
    },
    "workspace.daytona.sandbox_auto_delete_minutes": {
        "type": "number",
        "description": "Auto-delete interval on Daytona platform (minutes). Use -1 to disable. Default disabled.",
        "category": "workspace",
        "label": "Daytona Platform Auto-Delete",
        "min": -1,
        "step": 1,
    },
    "workspace.daytona.ephemeral": {
        "type": "boolean",
        "description": "Create ephemeral sandboxes that auto-delete when stopped.",
        "category": "workspace",
        "label": "Daytona Ephemeral",
    },
    "workspace.daytona.network_block_all": {
        "type": "boolean",
        "description": "Block all outbound network access for new sandboxes.",
        "category": "workspace",
        "label": "Daytona Block All Network",
    },
    "workspace.daytona.network_allow_list": {
        "type": "text",
        "description": "Comma-separated CIDR list for allowed outbound network (e.g. 10.0.0.0/8,172.16.0.0/12).",
        "category": "workspace",
        "label": "Daytona Network Allow List",
    },
    "workspace.modal.enabled": {
        "type": "boolean",
        "description": "Enable Modal sandbox workspaces.",
        "category": "workspace",
        "label": "Modal Enabled",
    },
    "workspace.modal.token_id": {
        "type": "password",
        "description": "Modal token ID. Leave empty to use the Modal SDK default credentials.",
        "category": "workspace",
        "label": "Modal Token ID",
    },
    "workspace.modal.token_secret": {
        "type": "password",
        "description": "Modal token secret. Leave empty to use the Modal SDK default credentials.",
        "category": "workspace",
        "label": "Modal Token Secret",
    },
    "workspace.modal.app_name": {
        "type": "text",
        "description": "Modal App name used to create sandbox workspaces.",
        "category": "workspace",
        "label": "Modal App Name",
    },
    "workspace.modal.default_root": {
        "type": "text",
        "description": "Default root directory inside Modal sandboxes.",
        "category": "workspace",
        "label": "Modal Default Root",
    },
    "workspace.modal.image": {
        "type": "text",
        "description": "Container image used for new Modal sandboxes.",
        "category": "workspace",
        "label": "Modal Image",
    },
    "workspace.modal.sandbox_timeout_seconds": {
        "type": "number",
        "description": "Maximum lifetime in seconds for new Modal sandboxes.",
        "category": "workspace",
        "label": "Modal Sandbox Timeout Seconds",
        "min": 60,
        "max": 86400,
        "step": 1,
    },
    "workspace.modal.idle_timeout_seconds": {
        "type": "number",
        "description": "Terminate idle Modal sandboxes after this many seconds. Use 0 for Modal default behavior.",
        "category": "workspace",
        "label": "Modal Idle Timeout Seconds",
        "min": 0,
        "max": 86400,
        "step": 1,
    },
    "workspace.openshell.enabled": {
        "type": "boolean",
        "description": "Enable NVIDIA OpenShell sandbox workspaces.",
        "category": "workspace",
        "label": "OpenShell Enabled",
    },
    "workspace.openshell.cli_path": {
        "type": "text",
        "description": "Path to the OpenShell CLI. Leave as openshell when it is available on PATH.",
        "category": "workspace",
        "label": "OpenShell CLI Path",
    },
    "workspace.openshell.default_root": {
        "type": "text",
        "description": "Default writable root directory inside OpenShell sandboxes.",
        "category": "workspace",
        "label": "OpenShell Default Root",
    },
    "workspace.openshell.image": {
        "type": "text",
        "description": "OpenShell sandbox image or community image name used for new workspaces.",
        "category": "workspace",
        "label": "OpenShell Image",
    },
    "workspace.openshell.cpu": {
        "type": "number",
        "description": "CPU cores for new OpenShell sandboxes. Use 0 for OpenShell defaults.",
        "category": "workspace",
        "label": "OpenShell CPU Cores",
        "min": 0,
        "step": 1,
    },
    "workspace.openshell.memory": {
        "type": "text",
        "description": "Memory limit for new OpenShell sandboxes, such as 2Gi or 4096Mi. Leave empty for defaults.",
        "category": "workspace",
        "label": "OpenShell Memory",
    },
    "workspace.openshell.create_timeout_seconds": {
        "type": "number",
        "description": "Maximum seconds to wait while creating an OpenShell sandbox.",
        "category": "workspace",
        "label": "OpenShell Create Timeout Seconds",
        "min": 1,
        "step": 1,
    },
    "workspace.openshell.exec_timeout_seconds": {
        "type": "number",
        "description": "Default timeout for OpenShell command execution.",
        "category": "workspace",
        "label": "OpenShell Exec Timeout Seconds",
        "min": 1,
        "step": 1,
    },
    "workspace.openshell.delete_timeout_seconds": {
        "type": "number",
        "description": "Maximum seconds to wait while deleting an OpenShell sandbox.",
        "category": "workspace",
        "label": "OpenShell Delete Timeout Seconds",
        "min": 1,
        "step": 1,
    },
    "workspace.openshell.list_timeout_seconds": {
        "type": "number",
        "description": "Maximum seconds to wait while listing OpenShell sandboxes.",
        "category": "workspace",
        "label": "OpenShell List Timeout Seconds",
        "min": 1,
        "step": 1,
    },
    REPOSITORY_SKILLS_DIR_KEY: {
        "type": "text",
        "description": "Repository-relative directory for repo-local skills.",
        "category": "skills",
        "label": "Repository Skills Directory",
    },
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
    "llm.default_model": {
        "type": "text",
        "description": "Default provider/model combination used as fallback or default for agentcards (e.g. gemini/gemma-4-31b-it)",
        "category": "llm",
        "label": "LLM Default Model",
    },
    LLM_FALLBACK_PROVIDER_KEY: {
        "type": "text",
        "description": "Provider name used when the agent's provider/model cannot be resolved (e.g. openai-main). Leave empty to disable.",
        "category": "llm",
        "label": "LLM Fallback Provider",
    },
    LLM_FALLBACK_MODEL_KEY: {
        "type": "text",
        "description": "Model name used together with the fallback provider. Leave empty to use the fallback provider's default model.",
        "category": "llm",
        "label": "LLM Fallback Model",
    },
    # Database settings
    "database.url": {
        "type": "url",
        "description": "Database connection URL (e.g., postgresql+asyncpg://user:password@host:5432/dbname)",
        "category": "database",
        "label": "Database URL",
    },
    # Display settings
    DISPLAY_TIMEZONE_CONFIG_KEY: {
        "type": "text",
        "description": "IANA timezone used to display dashboard timestamps (e.g. Asia/Bangkok)",
        "category": "general",
        "label": "Display Timezone",
    },
    # General / Workflows settings
    "recursion_limit": {
        "type": "number",
        "description": "Max recursion limit for LangGraph workflows",
        "category": "general",
        "label": "Recursion Limit",
        "min": 1,
        "max": 1000,
        "step": 1,
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
        "description": "Selectable models for providers without model listing; saved as a list",
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
    remainder = key[len(prefix) :]
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


def _channel_setting_metadata(key: str) -> dict[str, Any] | None:
    try:
        from agent.modules.channels import get_channel_setting_field
    except Exception:
        return None

    field = get_channel_setting_field(key)
    if field is None:
        return None
    return {
        "type": field.input_type,
        "description": field.description,
        "category": "channels",
        "label": field.label,
    }


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
        channel_meta = _channel_setting_metadata(key)
        if channel_meta is not None:
            return channel_meta
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
    "BOOTSTRAP_BOOLEAN_CONFIG_KEYS",
    "BOOTSTRAP_CONFIG_KEYS",
    "DEFAULT_CONFIG",
    "DEFAULT_DISPLAY_TIMEZONE",
    "DISPLAY_TIMEZONE_CONFIG_KEY",
    "LLM_FALLBACK_MODEL_KEY",
    "LLM_FALLBACK_PROVIDER_KEY",
    "REPOSITORY_SKILLS_DIR_KEY",
    "DATABASE_RUNTIME_KEY_PATTERNS",
    "RUNTIME_KEY_PATTERNS",
    "SENSITIVE_RUNTIME_KEY_PATTERNS",
    "is_runtime_key",
    "is_database_runtime_key",
    "is_sensitive_runtime_key",
    "KNOWN_RUNTIME_KEYS",
    "get_channel_enabled_key",
    "SETTING_METADATA",
    "get_setting_metadata",
    "parse_provider_key",
    "PROVIDER_SETTING_FIELD_ORDER",
]
