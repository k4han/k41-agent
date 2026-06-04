"""Centralized notification service for pushing messages to channel adapters."""

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

NotificationFormat = Literal["markdown", "html", "plain"]


def set_telegram_bot(bot: Any | None) -> None:
    """Compatibility shim for older callers that injected Telegram directly."""
    from agent.modules.channels import get_channel_registry, get_telegram_adapter

    adapter = get_telegram_adapter()
    adapter.set_bot(bot)
    get_channel_registry().register(adapter, replace=True)


def set_discord_client(client: Any | None) -> None:
    """Compatibility shim for older callers that injected Discord directly."""
    from agent.modules.channels import get_channel_registry, get_discord_adapter

    adapter = get_discord_adapter()
    adapter.set_client(client)
    get_channel_registry().register(adapter, replace=True)


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
            get_channel_registry,
            register_builtin_channel_adapters,
        )

        platform_name = str(getattr(platform, "value", platform) or "").strip().lower()
        register_builtin_channel_adapters()
        adapter = get_channel_registry().get(platform_name)
        if adapter is None:
            logger.warning("No channel adapter for platform %s.", platform_name)
            return False

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
