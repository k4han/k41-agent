import logging
import os

from agent.modules.agent_runtime.public import build_run_params, clear_agent_session, run_agent_full

logger = logging.getLogger(__name__)


async def handle_message(message) -> None:
    """Handle a default aiogram message."""

    params = build_run_params(
        platform="telegram",
        user_id=str(message.from_user.id),
        user_input=message.text,
        channel_id=str(message.chat.id),
        workflow="chat_agent",
    )

    await message.bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing",
    )

    response = await run_agent_full(**params)
    await message.answer(response)


def create_dispatcher():
    """Create an aiogram dispatcher and register handlers."""

    try:
        from aiogram import Dispatcher
        from aiogram.filters import Command, CommandStart
        from aiogram.types import Message
    except ImportError as exc:
        raise ImportError("Install: pip install aiogram") from exc

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
            "/research - Research & synthesis\n"
            "/clear    - Clear chat history"
        )

    @dp.message(Command("clear"))
    async def cmd_clear(message: Message):
        await clear_agent_session(
            platform="telegram",
            user_id=str(message.from_user.id),
            channel_id=str(message.chat.id),
        )
        await message.answer("Cuộc trò chuyện đã được xoá.")

    @dp.message(Command("code"))
    async def cmd_code(message: Message):
        text = message.text.removeprefix("/code").strip()
        if not text:
            await message.answer("Example: /code list files in directory")
            return

        params = build_run_params(
            platform="telegram",
            user_id=str(message.from_user.id),
            user_input=text,
            channel_id=str(message.chat.id),
            workflow="coding_agent",
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

        params = build_run_params(
            platform="telegram",
            user_id=str(message.from_user.id),
            user_input=text,
            channel_id=str(message.chat.id),
            workflow="research_chain",
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        response = await run_agent_full(**params)
        await message.answer(response)

    @dp.message()
    async def on_message(message: Message):
        if not message.text:
            return
        await handle_message(message)

    return dp


async def run_telegram_bot() -> None:
    """Start the Telegram bot with aiogram."""

    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
    except ImportError as exc:
        raise ImportError("Install: pip install aiogram") from exc

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
