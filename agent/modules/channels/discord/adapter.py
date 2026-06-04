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

logger = logging.getLogger(__name__)


class DiscordChannelAdapter:
    name = "discord"
    title = "Discord"
    summary = "Run agents inside Discord servers and DMs."
    tagline = "Bot platform"
    capabilities = frozenset({"chat", "outbound", "streaming"})
    settings_sections = (
        ChannelSettingSection(
            id="authentication",
            title="Authentication",
            subtitle="Bot token from Discord Developer Portal",
        ),
        ChannelSettingSection(
            id="agents",
            title="Agents",
            subtitle="Default agent and command routing",
            default_collapsed=True,
        ),
    )
    settings_schema = (
        ChannelSettingField(
            name="enabled",
            label="Discord Enabled",
            description="Enable Discord channel integration",
            input_type="boolean",
            section="authentication",
            default=True,
        ),
        ChannelSettingField(
            name="bot_token",
            label="Discord Bot Token",
            description="Discord bot token from Developer Portal",
            input_type="password",
            required=True,
            secret=True,
            section="authentication",
        ),
        ChannelSettingField(
            name="default_agent",
            label="Discord Default Agent",
            description="Default agent for Discord messages",
            section="agents",
        ),
        ChannelSettingField(
            name="code_agent",
            label="Discord Code Agent",
            description="Agent triggered by /code command",
            section="agents",
        ),
        ChannelSettingField(
            name="research_agent",
            label="Discord Research Agent",
            description="Agent triggered by /research command",
            section="agents",
        ),
    )

    def __init__(self) -> None:
        self._client: Any | None = None

    def create_runner(self):
        from agent.modules.channels.discord.handler import run_discord_bot

        return run_discord_bot

    def set_client(self, client: Any | None) -> None:
        self._client = client

    async def send(self, destination: str, message: OutboundMessage) -> bool:
        if self._client is None:
            logger.warning("No active Discord client to send outbound message.")
            return False

        try:
            uid = int(destination)
        except ValueError:
            logger.warning("Discord outbound destination must be a user id: %s", destination)
            return False

        discord_user = self._client.get_user(uid)
        if discord_user is None:
            discord_user = await self._client.fetch_user(uid)
        if discord_user is None:
            return False
        await discord_user.send(message.text)
        return True

    async def test_connection(self):
        from agent.modules.channels.diagnostics import test_discord_connection

        return await test_discord_connection()

    async def sync_commands(self, commands: Sequence[CommandSpec]) -> None:
        return None


_adapter = DiscordChannelAdapter()


def get_discord_adapter() -> DiscordChannelAdapter:
    return _adapter


async def handle_discord_message(message: Any) -> None:
    if getattr(getattr(message, "author", None), "bot", False):
        return
    content = str(getattr(message, "content", "") or "")
    if not content:
        return

    async def reply(outbound: OutboundMessage) -> Any:
        if outbound.update_target is not None and hasattr(outbound.update_target, "edit"):
            return await outbound.update_target.edit(content=outbound.text)
        return await message.reply(outbound.text)

    channel = getattr(message, "channel", None)
    author = getattr(message, "author", None)
    guild = getattr(message, "guild", None)
    inbound = InboundMessage(
        platform=get_discord_adapter().name,
        user_id=str(getattr(author, "id", "")),
        channel_id=str(getattr(channel, "id", "")),
        text=content,
        is_private=guild is None,
        raw=message,
        reply=reply,
    )
    await process_inbound_message(inbound, adapter=get_discord_adapter())


__all__ = [
    "DiscordChannelAdapter",
    "get_discord_adapter",
    "handle_discord_message",
]
