import logging
import os

from agent.modules.agent_runtime.public import build_run_params, clear_agent_session

logger = logging.getLogger(__name__)


def _resolve_catalog_agent_name(*candidates: str | None) -> str | None:
    """Return the first existing agent name from candidates, else None."""
    from agent.modules.agents.public import get_catalog_service

    catalog = get_catalog_service()
    for candidate in candidates:
        name = (candidate or "").strip()
        if not name:
            continue
        if catalog.get_agent(name) is not None:
            return name
    return None


async def handle_streaming_response(message, params) -> None:
    """Handle agent execution and stream UI updates for tool calls to telegram."""
    from agent.modules.agent_runtime.public import run_agent_stream
    from agent.modules.channels.infrastructure.telegram.formatter import (
        format_telegram_message,
        chunk_telegram_message,
    )
    from aiogram.enums import ParseMode

    status_text = "⏳ đang xử lí..."
    try:
        status_msg = await message.answer(status_text)
    except Exception as e:
        logger.error(f"Failed to send initial status: {e}")
        return

    tools_called = []
    final_response = ""

    async for event in run_agent_stream(**params):
        if event["type"] == "tool_call":
            tool_name = event["name"]
            args = event.get("args", {})
            # Ensure args is a dict/string and truncate if too long
            arg_str = str(args) if args else ""
            if len(arg_str) > 50:
                arg_str = arg_str[:47] + "..."

            tools_called.append(f"{tool_name}({arg_str})")

            # Update UI
            tools_ui = "\n".join(f"- 🔧 {t}" for t in tools_called)
            new_text = f"⏳ đang xử lí...\n{tools_ui}"

            try:
                await status_msg.edit_text(new_text)
            except Exception as e:
                logger.warning(f"Failed to edit status message: {e}")

        elif event["type"] == "final":
            final_response = event["content"]

    # Final formatting
    try:
        html_text = format_telegram_message(final_response)
        chunks = chunk_telegram_message(html_text)
    except Exception as e:
        logger.error(f"Error formatting message: {e}")
        chunks = [final_response]

    for i, chunk in enumerate(chunks):
        if i == 0:
            try:
                await status_msg.edit_text(chunk, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(
                    f"Failed to edit HTML chunk: {e}. Falling back to raw text."
                )
                try:
                    await status_msg.edit_text(chunk, parse_mode=None)
                except Exception as e2:
                    logger.error(f"Complete failure to edit message chunk: {e2}")
        else:
            try:
                await message.answer(chunk, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(
                    f"Failed to send HTML chunk: {e}. Falling back to raw text."
                )
                try:
                    await message.answer(chunk, parse_mode=None)
                except Exception as e2:
                    logger.error(f"Complete failure to send message chunk: {e2}")


async def handle_message(message) -> None:
    """Handle a default aiogram message."""

    default_agent_name = _resolve_catalog_agent_name(
        os.getenv("KAKA_TELEGRAM_DEFAULT_AGENT"),
        "default",
    )

    from agent.modules.workflows.infrastructure.langgraph.run_config import (
        DEFAULT_WORKING_DIR,
    )

    params = build_run_params(
        platform="telegram",
        user_id=str(message.from_user.id),
        user_input=message.text,
        channel_id=str(message.chat.id),
        working_dir=DEFAULT_WORKING_DIR,
        agent_name=default_agent_name,
    )

    await handle_streaming_response(message, params)


def create_dispatcher():
    """Create an aiogram dispatcher and register handlers."""

    try:
        from aiogram import Dispatcher
        from aiogram.filters import Command, CommandStart
        from aiogram.types import Message
    except ImportError as exc:
        raise ImportError("Install: pip install aiogram") from exc

    dp = Dispatcher()

    from agent.modules.agents.public import get_catalog_service

    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        await message.answer("Hello! I am an AI assistant.\nType anything to start.")

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

        code_agent_name = _resolve_catalog_agent_name(
            os.getenv("KAKA_TELEGRAM_CODE_AGENT"),
            "code-agent",
            "coder",
        )

        params = build_run_params(
            platform="telegram",
            user_id=str(message.from_user.id),
            user_input=text,
            channel_id=str(message.chat.id),
            service_type="backend",
            agent_name=code_agent_name,
        )
        await handle_streaming_response(message, params)

    @dp.message(Command("research"))
    async def cmd_research(message: Message):
        text = message.text.removeprefix("/research").strip()
        if not text:
            await message.answer("Example: /research pros and cons of microservices")
            return

        research_agent_name = _resolve_catalog_agent_name(
            os.getenv("KAKA_TELEGRAM_RESEARCH_AGENT"),
            "research-agent",
            "researcher",
        )

        params = build_run_params(
            platform="telegram",
            user_id=str(message.from_user.id),
            user_input=text,
            channel_id=str(message.chat.id),
            workflow="research_chain" if research_agent_name is None else None,
            agent_name=research_agent_name,
        )
        await handle_streaming_response(message, params)

    @dp.message(Command("agent"))
    async def cmd_agent(message: Message):
        """Run a specific agent: /agent <name> <task>"""

        text = message.text.removeprefix("/agent").strip()
        if not text:
            await message.answer("Example: /agent researcher Find info about AI")
            return

        catalog = get_catalog_service()
        agents = catalog.list_agents()
        if not agents:
            await message.answer("No custom agents defined. Use /code or /research.")
            return

        # First word is agent name, rest is task
        parts = text.split(None, 1)
        agent_name = parts[0]
        task = parts[1] if len(parts) > 1 else ""

        if not task:
            await message.answer("Example: /agent researcher Find info about AI")
            return

        agent_config = catalog.get_agent(agent_name)
        if agent_config is None:
            available = ", ".join(a.name for a in agents)
            await message.answer(
                f"Agent '{agent_name}' không tồn tại. Có sẵn: {available}"
            )
            return

        params = build_run_params(
            platform="telegram",
            user_id=str(message.from_user.id),
            user_input=task,
            channel_id=str(message.chat.id),
            agent_name=agent_name,
        )
        await handle_streaming_response(message, params)

    @dp.message(Command("agents"))
    async def cmd_agents(message: Message):
        """List all available agents from MD files."""

        catalog = get_catalog_service()
        agents = catalog.list_agents()
        if not agents:
            await message.answer(
                "No custom agents defined.\n"
                "Use /code or /research, or create .md files in ~/.kaka-agent/agents/ "
                "(legacy: ~/.kaka-agent/subagents/)"
            )
            return

        lines = ["Available agents:"]
        for a in agents:
            dn = a.display_name or a.name
            sub = (
                f" (can call: {', '.join(a.sub_agents)})"
                if a.sub_agents is not None
                else ""
            )
            lines.append(
                f"- <b>{dn}</b> <code>{a.name}</code> — graph: {a.graph_type}{sub}"
            )

        await message.answer("\n".join(lines))

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
