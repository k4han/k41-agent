"""Public interface for the notifications module."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    # interruption
    "active_manager": "agent.modules.notifications.interruption",
    "identities_query": "agent.modules.notifications.interruption",
    "InterruptionTimeoutManager": "agent.modules.notifications.interruption",
    "RunAdmissionConflict": "agent.modules.notifications.interruption",
    "ResponseConflict": "agent.modules.notifications.interruption",
    # settings
    "NotificationSettings": "agent.modules.notifications.settings",
    "load_settings": "agent.modules.notifications.settings",
    "CHANNELS": "agent.modules.notifications.settings",
    # service
    "send_notification": "agent.modules.notifications.service",
    "set_discord_client": "agent.modules.notifications.service",
    "set_telegram_bot": "agent.modules.notifications.service",
    # failures
    "report_failure": "agent.modules.notifications.failures",
    "report_provider_limit": "agent.modules.notifications.failures",
    "background_scope": "agent.modules.notifications.failures",
    # telegram
    "handle_reply": "agent.modules.notifications.telegram",
    "PREFIX": "agent.modules.notifications.telegram",
    "handle_callback": "agent.modules.notifications.telegram",
    # models
    "InterruptionRecord": "agent.modules.notifications.models",
    "NotificationDelivery": "agent.modules.notifications.models",
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)
