import logging
import time
from collections import deque
from collections.abc import Sequence

from agent.modules.channels.telegram.sender import send_telegram_response

logger = logging.getLogger(__name__)

STATUS_EDIT_INTERVAL_SECONDS = 1.0
MAX_VISIBLE_TOOL_CALLS = 8


def _format_status_text(tools_called: Sequence[str], total_tool_calls: int = 0) -> str:
    if not tools_called:
        return "Processing..."

    tools_ui = "\n".join(f"- {tool}" for tool in tools_called)
    hidden_count = total_tool_calls - len(tools_called)
    if hidden_count > 0:
        tools_ui = f"- {hidden_count} earlier tool call(s)\n{tools_ui}"
    return f"Processing...\n{tools_ui}"


async def handle_streaming_response(message, params) -> None:
    """Handle agent execution and stream UI updates for tool calls to Telegram."""
    from agent.modules.agent_runtime import run_agent_stream

    status_text = _format_status_text([])
    try:
        status_msg = await message.answer(status_text)
    except Exception as e:
        logger.error("Failed to send initial status: %s", e)
        return

    tools_called = deque(maxlen=MAX_VISIBLE_TOOL_CALLS)
    total_tool_calls = 0
    final_response = ""
    last_status_edit_at = 0.0

    async for event in run_agent_stream(**params):
        if event["type"] == "tool_call":
            tool_name = event["name"]
            args = event.get("args", {})
            arg_str = str(args) if args else ""
            if len(arg_str) > 50:
                arg_str = arg_str[:47] + "..."

            tools_called.append(f"{tool_name}({arg_str})")
            total_tool_calls += 1

            now = time.monotonic()
            should_edit = (
                total_tool_calls == 1
                or now - last_status_edit_at >= STATUS_EDIT_INTERVAL_SECONDS
            )
            if not should_edit:
                continue

            new_text = _format_status_text(tools_called, total_tool_calls)

            try:
                await status_msg.edit_text(new_text)
                last_status_edit_at = now
            except Exception as e:
                logger.warning("Failed to edit status message: %s", e)

        elif event["type"] == "final":
            final_response = event["content"]

    await send_telegram_response(
        message,
        status_msg,
        final_response or "No response.",
        mode="markdown",
    )
