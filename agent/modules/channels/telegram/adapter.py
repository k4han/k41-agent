from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from agent.modules.channels.commands import CommandSpec
from agent.modules.channels.contracts import (
    ChannelSettingField,
    ChannelSettingSection,
    InboundMessage,
    OutboundMessage,
)
from agent.modules.channels.pipeline import process_inbound_message
from agent.modules.channels.telegram.sender import (
    answer_telegram_message,
    send_telegram_bot_message,
    send_telegram_response,
)

logger = logging.getLogger(__name__)


class TelegramChannelAdapter:
    name = "telegram"
    title = "Telegram"
    summary = "Chat with your agents from Telegram private chats."
    tagline = "Bot platform"
    capabilities = frozenset({"chat", "outbound", "streaming", "command_suggestions", "private_only"})
    settings_sections = (
        ChannelSettingSection(
            id="authentication",
            title="Authentication",
            subtitle="Bot credentials from @BotFather",
        ),
        ChannelSettingSection(
            id="agents",
            title="Agents",
            subtitle="Default agent and command routing",
            default_collapsed=True,
        ),
        ChannelSettingSection(
            id="webhook",
            title="Update Mode",
            subtitle="Polling or webhook delivery",
            default_collapsed=True,
        ),
    )
    settings_schema = (
        ChannelSettingField(
            name="enabled",
            label="Telegram Enabled",
            description="Enable Telegram channel integration",
            input_type="boolean",
            section="authentication",
            default=True,
        ),
        ChannelSettingField(
            name="bot_token",
            label="Telegram Bot Token",
            description="Telegram bot token from @BotFather",
            input_type="password",
            required=True,
            secret=True,
            section="authentication",
        ),
        ChannelSettingField(
            name="default_agent",
            label="Telegram Default Agent",
            description="Default agent for Telegram messages",
            section="agents",
        ),
        ChannelSettingField(
            name="code_agent",
            label="Telegram Code Agent",
            description="Agent triggered by /code command",
            section="agents",
        ),
        ChannelSettingField(
            name="research_agent",
            label="Telegram Research Agent",
            description="Agent triggered by /research command",
            section="agents",
        ),
        ChannelSettingField(
            name="update_mode",
            label="Telegram Update Mode",
            description="Telegram update mode: polling or webhook",
            section="webhook",
            default="polling",
        ),
        ChannelSettingField(
            name="webhook_url",
            label="Telegram Webhook URL",
            description="Public HTTPS endpoint for Telegram webhook mode",
            input_type="url",
            section="webhook",
        ),
        ChannelSettingField(
            name="webhook_secret",
            label="Telegram Webhook Secret",
            description="Secret token checked against Telegram webhook requests",
            input_type="password",
            secret=True,
            section="webhook",
        ),
    )

    def __init__(self) -> None:
        self._bot: Any | None = None

    def create_runner(self):
        from agent.modules.channels.telegram.bot import run_telegram_bot

        return run_telegram_bot

    def set_bot(self, bot: Any | None) -> None:
        self._bot = bot

    async def send(self, destination: str, message: OutboundMessage) -> bool:
        if self._bot is None:
            logger.warning("No active Telegram bot to send outbound message.")
            return False
        sent = await send_telegram_bot_message(
            self._bot,
            destination,
            message.text,
            mode=message.mode,
        )
        return bool(sent)

    async def test_connection(self):
        from agent.modules.channels.diagnostics import test_telegram_connection

        return await test_telegram_connection()

    async def sync_commands(self, commands: Sequence[CommandSpec]) -> None:
        if self._bot is None:
            return
        try:
            from aiogram.types import BotCommand
        except ImportError:
            return

        bot_commands = [
            BotCommand(command=spec.name, description=spec.summary[:256])
            for spec in commands
            if spec.suggestable
        ]
        try:
            await self._bot.set_my_commands(bot_commands)
        except Exception as exc:
            logger.warning("Failed to sync Telegram bot commands: %s", exc)


_adapter = TelegramChannelAdapter()


def get_telegram_adapter() -> TelegramChannelAdapter:
    return _adapter


def _is_private_chat(event: Any) -> bool:
    chat = getattr(event, "chat", None)
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return True
    chat_type_value = getattr(chat_type, "value", str(chat_type))
    return chat_type_value == "private"


async def handle_telegram_message(message: Any) -> None:
    if not getattr(message, "text", None) or not getattr(message, "from_user", None):
        return

    async def reply(outbound: OutboundMessage) -> Any:
        if outbound.update_target is not None:
            sent = await send_telegram_response(
                message,
                outbound.update_target,
                outbound.text,
                mode=outbound.mode,
            )
        else:
            sent = await answer_telegram_message(
                message,
                outbound.text,
                mode=outbound.mode,
            )
        return sent[0] if sent else None

    inbound = InboundMessage(
        platform=get_telegram_adapter().name,
        user_id=str(message.from_user.id),
        channel_id=str(message.chat.id),
        text=message.text,
        is_private=_is_private_chat(message),
        raw=message,
        reply=reply,
    )
    await process_inbound_message(inbound, adapter=get_telegram_adapter())


__all__ = [
    "TelegramChannelAdapter",
    "get_telegram_adapter",
    "handle_telegram_message",
]
