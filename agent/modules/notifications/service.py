"""Centralized notification service for pushing messages to channel clients.

This module holds references to the Telegram bot and Discord client and
provides a single ``send_notification`` coroutine that any part of the
application (scheduler, background tasks, etc.) can call to deliver a
message to a user on their preferred platform.
"""

import logging
from typing import Any, Optional

from agent.modules.users import Platform

logger = logging.getLogger(__name__)

_telegram_bot: Optional[Any] = None
_discord_client: Optional[Any] = None


def set_telegram_bot(bot: Any) -> None:
    """Store the Telegram bot instance for later notification use."""
    global _telegram_bot
    _telegram_bot = bot


def set_discord_client(client: Any) -> None:
    """Store the Discord client instance for later notification use."""
    global _discord_client
    _discord_client = client


async def send_notification(platform: str, external_id: str, message: str) -> bool:
    """Send a push notification via the appropriate channel client.

    Returns True if the message was sent successfully, False otherwise.
    """
    try:
        if platform == Platform.TELEGRAM and _telegram_bot:
            await _telegram_bot.send_message(
                chat_id=external_id, text=message, parse_mode="HTML"
            )
            return True

        if platform == Platform.DISCORD and _discord_client:
            uid = int(external_id)
            discord_user = _discord_client.get_user(uid)
            if discord_user is None:
                discord_user = await _discord_client.fetch_user(uid)
            if discord_user:
                await discord_user.send(message)
                return True

        logger.warning(
            "No active client for platform %s to send notification.", platform
        )
    except Exception as exc:
        logger.error(
            "Failed to send notification to %s user %s: %s",
            platform,
            external_id,
            exc,
        )
    return False
