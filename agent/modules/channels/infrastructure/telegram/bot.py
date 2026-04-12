import asyncio
import contextlib
import logging

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value
from agent.modules.scheduler.public import set_telegram_bot
from agent.modules.channels.infrastructure.telegram.commands import (
    auth_middleware,
    cmd_start,
    cmd_help,
    cmd_clear,
    cmd_code,
    cmd_research,
    cmd_agent,
    cmd_agents,
    on_message,
    resolve_agent_config,
)

logger = logging.getLogger(__name__)


def create_dispatcher():
    """Create an aiogram dispatcher and register handlers."""

    try:
        from aiogram import Dispatcher
        from aiogram.filters import Command, CommandStart
        from aiogram.types import Message
    except ImportError as exc:
        raise ImportError("Install: pip install aiogram") from exc

    dp = Dispatcher()

    resolve_agent_config()

    dp.message.outer_middleware()(auth_middleware)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_clear, Command("clear"))
    dp.message.register(cmd_code, Command("code"))
    dp.message.register(cmd_research, Command("research"))
    dp.message.register(cmd_agent, Command("agent"))
    dp.message.register(cmd_agents, Command("agents"))

    dp.message.register(on_message)

    return dp


async def run_telegram_bot() -> None:
    """Start the Telegram bot with aiogram."""

    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
    except ImportError as exc:
        raise ImportError("Install: pip install aiogram") from exc

    config = get_config_service()
    token = config.get_str("channels.telegram.bot_token", "")

    if is_placeholder_value(token):
        raise ValueError(
            "Telegram bot token not configured. "
            "Set 'channels.telegram.bot_token' in ~/.kaka-agent/config.yaml"
        )

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()
    set_telegram_bot(bot)

    logger.info("Telegram bot starting...")
    try:
        await dp.start_polling(bot, close_bot_session=False)
    except asyncio.CancelledError:
        logger.info("Telegram polling cancelled.")
        raise
    finally:
        # Keep shutdown deterministic when channel task is cancelled.
        with contextlib.suppress(Exception):
            await bot.session.close()