"""Centralized notification service for pushing messages to channel adapters."""

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

NotificationFormat = Literal["markdown", "html", "plain"]


def _inject_channel_attribute(name: str, attr: str, value: Any | None) -> None:
    """Apply ``set_<attr>`` on the registered adapter, re-registering when the
    setter returns a new instance so callers always see the latest reference.
    """
    from agent.modules.channels import get_channel_registry, load_channel_adapter

    adapter = load_channel_adapter(name)
    setter = getattr(adapter, f"set_{attr}", None)
    if not callable(setter):
        return
    result = setter(value)
    replacement = result if result is not None and result is not adapter else adapter
    if replacement is not adapter:
        get_channel_registry().register(replacement, replace=True)


def set_telegram_bot(bot: Any | None) -> None:
    """Compatibility shim for older callers that injected Telegram directly."""
    _inject_channel_attribute("telegram", "bot", bot)


def set_discord_client(client: Any | None) -> None:
    """Compatibility shim for older callers that injected Discord directly."""
    _inject_channel_attribute("discord", "client", client)


async def send_notification(
    platform: str,
    external_id: str,
    message: str,
    *,
    mode: NotificationFormat = "html",
) -> bool:
    """Send a push notification via the appropriate channel client.

    Returns True if the message was sent successfully, False otherwise.
    """
    try:
        from agent.modules.channels import (
            OutboundMessage,
            load_channel_adapter,
            register_builtin_channel_adapters,
        )

        platform_name = str(getattr(platform, "value", platform) or "").strip().lower()
        register_builtin_channel_adapters()
        adapter = load_channel_adapter(platform_name)

        return await adapter.send(
            external_id,
            OutboundMessage(text=message, mode=mode),
        )
    except Exception as exc:
        logger.error(
            "Failed to send notification to %s user %s: %s",
            platform,
            external_id,
            exc,
        )
    return False
