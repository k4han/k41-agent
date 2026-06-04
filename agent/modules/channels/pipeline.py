from __future__ import annotations

import logging

from agent.modules.channels.agent_bridge import stream_default_agent_response
from agent.modules.channels.commands import CommandRegistry, get_default_command_registry
from agent.modules.channels.contracts import ChatChannelAdapter, InboundMessage, OutboundMessage
from agent.modules.users import authenticate_channel_message

logger = logging.getLogger(__name__)


async def process_inbound_message(
    message: InboundMessage,
    *,
    adapter: ChatChannelAdapter | None = None,
    commands: CommandRegistry | None = None,
) -> None:
    if not message.text:
        return

    if adapter is not None and "private_only" in adapter.capabilities and not message.is_private:
        logger.info(
            "Ignoring non-private %s message from channel_id=%s",
            message.platform,
            message.channel_id,
        )
        return

    registry = commands or get_default_command_registry()
    parsed = registry.parse(message.text)
    if parsed is not None and registry.is_public(parsed.name):
        await registry.dispatch(message, parsed)
        return

    if not await _authenticate(message):
        return

    if parsed is not None:
        await registry.dispatch(message, parsed)
        return

    await stream_default_agent_response(message)


async def _authenticate(message: InboundMessage) -> bool:
    async def reply_fn(text: str) -> None:
        if message.reply is None:
            return
        await message.reply(OutboundMessage(text, mode="plain"))

    return await authenticate_channel_message(
        message.platform,
        message.user_id,
        message.text,
        reply_fn,
    )


__all__ = ["process_inbound_message"]
