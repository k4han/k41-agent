import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value
from agent.modules.channels.commands import get_default_command_registry
from agent.modules.channels.telegram.adapter import (
    get_telegram_adapter,
    handle_telegram_message,
)

logger = logging.getLogger(__name__)

TELEGRAM_UPDATE_MODE_POLLING = "polling"
TELEGRAM_UPDATE_MODE_WEBHOOK = "webhook"


@dataclass(frozen=True, slots=True)
class TelegramWebhookRuntime:
    bot: Any
    dispatcher: Any
    secret: str


_telegram_webhook_runtime: TelegramWebhookRuntime | None = None


def get_telegram_webhook_runtime() -> TelegramWebhookRuntime | None:
    return _telegram_webhook_runtime


def set_telegram_webhook_runtime(runtime: TelegramWebhookRuntime | None) -> None:
    global _telegram_webhook_runtime
    _telegram_webhook_runtime = runtime


def _resolve_update_mode(value: str) -> str:
    mode = (value or TELEGRAM_UPDATE_MODE_POLLING).strip().lower()
    if mode not in {TELEGRAM_UPDATE_MODE_POLLING, TELEGRAM_UPDATE_MODE_WEBHOOK}:
        raise ValueError(
            "Invalid channels.telegram.update_mode. Use 'polling' or 'webhook'."
        )
    return mode


def create_bot(token: str):
    """Create an aiogram bot instance."""

    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
    except ImportError as exc:
        raise ImportError("Install aiogram with uv before enabling Telegram.") from exc

    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher():
    """Create an aiogram dispatcher and register handlers."""

    try:
        from aiogram import Dispatcher
    except ImportError as exc:
        raise ImportError("Install aiogram with uv before enabling Telegram.") from exc

    dp = Dispatcher()
    dp.message.register(handle_telegram_message)

    return dp


async def _run_polling_bot(bot: Any, dispatcher: Any) -> None:
    logger.info("Telegram bot starting in polling mode...")
    try:
        with contextlib.suppress(Exception):
            await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(bot, close_bot_session=False)
    except asyncio.CancelledError:
        logger.info("Telegram polling cancelled.")
        raise
    finally:
        with contextlib.suppress(Exception):
            await bot.session.close()
        get_telegram_adapter().set_bot(None)


async def _run_webhook_bot(
    bot: Any,
    dispatcher: Any,
    *,
    webhook_url: str,
    webhook_secret: str,
) -> None:
    logger.info("Telegram bot starting in webhook mode...")
    try:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=webhook_secret,
            drop_pending_updates=False,
        )
        set_telegram_webhook_runtime(
            TelegramWebhookRuntime(
                bot=bot,
                dispatcher=dispatcher,
                secret=webhook_secret,
            )
        )
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("Telegram webhook runtime cancelled.")
        raise
    finally:
        set_telegram_webhook_runtime(None)
        with contextlib.suppress(Exception):
            await bot.delete_webhook(drop_pending_updates=False)
        with contextlib.suppress(Exception):
            await bot.session.close()
        get_telegram_adapter().set_bot(None)


async def run_telegram_bot() -> None:
    """Start the Telegram bot with aiogram."""
    config = get_config_service()
    token = config.get_str("channels.telegram.bot_token", "")

    if is_placeholder_value(token):
        raise ValueError(
            "Telegram bot token not configured. "
            "Set 'channels.telegram.bot_token' in the dashboard channel settings."
        )

    update_mode = _resolve_update_mode(
        config.get_str("channels.telegram.update_mode", TELEGRAM_UPDATE_MODE_POLLING)
    )
    webhook_url = ""
    webhook_secret = ""
    if update_mode == TELEGRAM_UPDATE_MODE_WEBHOOK:
        webhook_url = config.get_str("channels.telegram.webhook_url", "")
        webhook_secret = config.get_str("channels.telegram.webhook_secret", "")
        if is_placeholder_value(webhook_url):
            raise ValueError(
                "Telegram webhook URL not configured. "
                "Set 'channels.telegram.webhook_url' in the dashboard channel settings."
            )
        if is_placeholder_value(webhook_secret):
            raise ValueError(
                "Telegram webhook secret not configured. "
                "Set 'channels.telegram.webhook_secret' in the dashboard channel settings."
            )

    dp = create_dispatcher()
    bot = create_bot(token)
    get_telegram_adapter().set_bot(bot)
    await get_telegram_adapter().sync_commands(get_default_command_registry().list())

    if update_mode == TELEGRAM_UPDATE_MODE_POLLING:
        await _run_polling_bot(bot, dp)
        return

    await _run_webhook_bot(
        bot,
        dp,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )
