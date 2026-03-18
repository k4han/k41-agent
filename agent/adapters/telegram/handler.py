# agent/adapters/telegram/handler.py
#
# Dùng aiogram (async-native, hiệu năng cao hơn python-telegram-bot)
# Cài: pip install aiogram

import os
import logging

from agent.adapters.base import BaseAdapter
from agent.core.runner   import run_agent_full

logger = logging.getLogger(__name__)


class TelegramAdapter(BaseAdapter):
    async def handle(self, message) -> None:
        """
        Handler cho aiogram Message.
        message: aiogram.types.Message
        """
        params = self.normalize(
            platform=   "telegram",
            user_id=    str(message.from_user.id),
            user_input=  message.text,
            channel_id= str(message.chat.id),
            workflow=   "chat_agent",
        )

        # Hiển thị "đang gõ..."
        await message.bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing",
        )

        response = await run_agent_full(**params)
        await message.answer(response)


adapter = TelegramAdapter()


def create_dispatcher():
    """Tạo aiogram Dispatcher và đăng ký handlers."""
    try:
        from aiogram import Dispatcher
        from aiogram.filters import CommandStart, Command
        from aiogram.types import Message
    except ImportError:
        raise ImportError("Cài đặt: pip install aiogram")

    dp = Dispatcher()

    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        await message.answer(
            "Xin chào! Tôi là AI assistant.\n"
            "Gõ bất kỳ nội dung để bắt đầu."
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(
            "Các lệnh:\n"
            "/start    - Bắt đầu\n"
            "/help     - Trợ giúp\n"
            "/code     - Coding assistant\n"
            "/research - Nghiên cứu & tổng hợp"
        )

    @dp.message(Command("code"))
    async def cmd_code(message: Message):
        text = message.text.removeprefix("/code").strip()
        if not text:
            await message.answer("Ví dụ: /code liệt kê file trong thư mục")
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
            await message.answer("Ví dụ: /research ưu nhược điểm microservices")
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
        """Handler mặc định — tất cả tin nhắn thường."""
        if not message.text:
            return
        await adapter.handle(message)

    return dp


async def run_telegram_bot() -> None:
    """Khởi động Telegram bot với aiogram."""
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
    except ImportError:
        raise ImportError("Cài đặt: pip install aiogram")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Thiếu TELEGRAM_BOT_TOKEN trong .env")

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()

    logger.info("Telegram bot starting...")
    await dp.start_polling(bot)
