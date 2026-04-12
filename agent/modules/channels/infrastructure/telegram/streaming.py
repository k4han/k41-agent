import logging

from agent.modules.channels.infrastructure.telegram.formatter import (
    format_telegram_message,
    chunk_telegram_message,
)

logger = logging.getLogger(__name__)


async def handle_streaming_response(message, params) -> None:
    """Handle agent execution and stream UI updates for tool calls to telegram."""
    from agent.modules.agent_runtime.public import run_agent_stream
    from aiogram.enums import ParseMode

    status_text = "⏳ đang xử lí..."
    try:
        status_msg = await message.answer(status_text)
    except Exception as e:
        logger.error("Failed to send initial status: %s", e)
        return

    tools_called = []
    final_response = ""

    async for event in run_agent_stream(**params):
        if event["type"] == "tool_call":
            tool_name = event["name"]
            args = event.get("args", {})
            arg_str = str(args) if args else ""
            if len(arg_str) > 50:
                arg_str = arg_str[:47] + "..."

            tools_called.append(f"{tool_name}({arg_str})")

            tools_ui = "\n".join(f"- 🔧 {t}" for t in tools_called)
            new_text = f"⏳ đang xử lí...\n{tools_ui}"

            try:
                await status_msg.edit_text(new_text)
            except Exception as e:
                logger.warning("Failed to edit status message: %s", e)

        elif event["type"] == "final":
            final_response = event["content"]

    try:
        html_text = format_telegram_message(final_response)
        chunks = chunk_telegram_message(html_text)
    except Exception as e:
        logger.error("Error formatting message: %s", e)
        chunks = [final_response]

    for i, chunk in enumerate(chunks):
        if i == 0:
            try:
                await status_msg.edit_text(chunk, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(
                    "Failed to edit HTML chunk: %s. Falling back to raw text.", e
                )
                try:
                    await status_msg.edit_text(chunk, parse_mode=None)
                except Exception as e2:
                    logger.error("Complete failure to edit message chunk: %s", e2)
        else:
            try:
                await message.answer(chunk, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(
                    "Failed to send HTML chunk: %s. Falling back to raw text.", e
                )
                try:
                    await message.answer(chunk, parse_mode=None)
                except Exception as e2:
                    logger.error("Complete failure to send message chunk: %s", e2)