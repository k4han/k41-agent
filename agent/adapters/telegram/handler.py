# agent/adapters/telegram/handler.py
#
# Uses aiogram (async-native, higher performance than python-telegram-bot)
# Install: pip install aiogram

import os
import logging

from agent.adapters.base import BaseAdapter
from agent.core.runner   import run_agent_full

logger = logging.getLogger(__name__)


class TelegramAdapter(BaseAdapter):
    async def handle(self, message) -> None:
        """
        Handler for aiogram Message.
        message: aiogram.types.Message
        """
        params = self.normalize(
            platform=   "telegram",
            user_id=    str(message.from_user.id),
            user_input=  message.text,
            channel_id= str(message.chat.id),
            workflow=   "chat_agent",
        )

        # Show typing indicator
        await message.bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing",
        )

        response = await run_agent_full(**params)
        await message.answer(response)


adapter = TelegramAdapter()


def create_dispatcher():
    """Create aiogram Dispatcher and register handlers."""
    try:
        from aiogram import Dispatcher
        from aiogram.filters import CommandStart, Command
        from aiogram.types import Message
    except ImportError:
        raise ImportError("Install: pip install aiogram")

    dp = Dispatcher()

    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        await message.answer(
            "Hello! I am an AI assistant.\n"
            "Type anything to start."
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(
            "Commands:\n"
            "/start    - Start\n"
            "/help     - Help\n"
            "/code     - Coding assistant\n"
            "/research - Research & synthesis"
        )

    @dp.message(Command("code"))
    async def cmd_code(message: Message):
        text = message.text.removeprefix("/code").strip()
        if not text:
            await message.answer("Example: /code list files in directory")
            return

        params = adapter.normalize(
            platform=    "telegram",
            user_id=     str(message.from_user.id),
            user_input=  text,
            channel_id=  str(message.chat.id),
            workflow=    "coding_agent",
            service_type="backend",
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        response = await run_agent_full(**params)
        await message.answer(response)

    @dp.message(Command("research"))
    async def cmd_research(message: Message):
        text = message.text.removeprefix("/research").strip()
        if not text:
            await message.answer("Example: /research pros and cons of microservices")
            return

        params = adapter.normalize(
            platform=   "telegram",
            user_id=    str(message.from_user.id),
            user_input=  text,
            channel_id= str(message.chat.id),
            workflow=   "research_chain",
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        response = await run_agent_full(**params)
        await message.answer(response)

    @dp.message()
    async def on_message(message: Message):
        """Default handler — all regular messages."""
        if not message.text:
            return
        await adapter.handle(message)

    return dp


async def run_telegram_bot() -> None:
    """Start Telegram bot with aiogram."""
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
    except ImportError:
        raise ImportError("Install: pip install aiogram")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN in .env")

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()

    logger.info("Telegram bot starting...")
    await dp.start_polling(bot)
