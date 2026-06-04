from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Sequence
from typing import Any

from agent.modules.agent_runtime import build_run_params
from agent.modules.agents import resolve_catalog_agent_name
from agent.modules.channels.contracts import InboundMessage, OutboundMessage
from agent.modules.workflows import DEFAULT_WORKING_DIR
from agent.shared.config import get_config_service
from agent.shared.infrastructure.errors import classify_agent_error

logger = logging.getLogger(__name__)

STATUS_EDIT_INTERVAL_SECONDS = 1.0
MAX_VISIBLE_TOOL_CALLS = 8


def format_status_text(tools_called: Sequence[str], total_tool_calls: int = 0) -> str:
    if not tools_called:
        return "Processing..."

    tools_ui = "\n".join(f"- {tool}" for tool in tools_called)
    hidden_count = total_tool_calls - len(tools_called)
    if hidden_count > 0:
        tools_ui = f"- {hidden_count} earlier tool call(s)\n{tools_ui}"
    return f"Processing...\n{tools_ui}"


def resolve_default_agent_name(platform: str) -> str:
    config = get_config_service()
    configured = config.get_str(f"channels.{platform}.default_agent", "")
    return resolve_catalog_agent_name(configured, "default-agent", "default") or "default"


def resolve_code_agent_name(platform: str) -> str:
    config = get_config_service()
    configured = config.get_str(f"channels.{platform}.code_agent", "")
    return resolve_catalog_agent_name(configured, "code-agent", "coder", "default") or "default"


def resolve_research_agent_name(platform: str) -> str | None:
    config = get_config_service()
    configured = config.get_str(f"channels.{platform}.research_agent", "")
    return resolve_catalog_agent_name(configured, "research-agent", "researcher")


def build_channel_run_params(
    message: InboundMessage,
    agent_name: str | None,
    *,
    user_input: str | None = None,
    workflow: str | None = None,
    working_dir: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    return build_run_params(
        platform=message.platform,
        user_id=message.user_id,
        user_input=message.text if user_input is None else user_input,
        channel_id=message.channel_id,
        agent_name=agent_name or "default",
        workflow=workflow,
        working_dir=working_dir,
        **overrides,
    )


async def stream_agent_response(message: InboundMessage, params: dict[str, Any]) -> None:
    if message.reply is None:
        logger.warning("Cannot stream response for %s without reply callback.", message.platform)
        return

    from agent.modules.agent_runtime import run_agent_stream

    try:
        status_target = await message.reply(
            OutboundMessage(format_status_text([]), mode="plain")
        )
    except Exception as exc:
        logger.error("Failed to send initial channel status: %s", exc)
        return

    tools_called = deque(maxlen=MAX_VISIBLE_TOOL_CALLS)
    total_tool_calls = 0
    final_response = ""
    message_chunks: list[str] = []
    last_status_edit_at = 0.0

    try:
        async for event in run_agent_stream(**params):
            event_type = str(event.get("type") or "")
            if event_type == "tool_call":
                tool_name = str(event.get("name") or "unknown")
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

                await message.reply(
                    OutboundMessage(
                        format_status_text(tools_called, total_tool_calls),
                        mode="plain",
                        update_target=status_target,
                    )
                )
                last_status_edit_at = now
            elif event_type == "message":
                content = event.get("content")
                if isinstance(content, str):
                    message_chunks.append(content)
            elif event_type == "final":
                content = event.get("content")
                if isinstance(content, str):
                    final_response = content
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        logger.exception("Agent stream failed for %s message.", message.platform)
        agent_error = classify_agent_error(exc)
        await message.reply(
            OutboundMessage(
                agent_error.message,
                mode="plain",
                update_target=status_target,
            )
        )
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return

    await message.reply(
        OutboundMessage(
            final_response or "".join(message_chunks) or "No response.",
            mode="markdown",
            update_target=status_target,
        )
    )


async def stream_default_agent_response(message: InboundMessage) -> None:
    params = build_channel_run_params(
        message,
        resolve_default_agent_name(message.platform),
        working_dir=DEFAULT_WORKING_DIR,
    )
    await stream_agent_response(message, params)


__all__ = [
    "build_channel_run_params",
    "format_status_text",
    "resolve_code_agent_name",
    "resolve_default_agent_name",
    "resolve_research_agent_name",
    "stream_agent_response",
    "stream_default_agent_response",
]
