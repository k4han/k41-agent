# agent/adapters/discord/handler.py
#
# Install: pip install discord.py
# Setup: create bot at discord.com/developers, get token, add to .env

import os
import logging

from agent.adapters.base import BaseAdapter
from agent.core.runner   import run_agent_full

logger = logging.getLogger(__name__)


class DiscordAdapter(BaseAdapter):
    async def handle(self, message):
        """
        Handler for discord message.
        message: discord.Message
        """
        # Skip bot messages
        if message.author.bot:
            return

        params = self.normalize(
            platform=   "discord",
            user_id=    str(message.author.id),
            user_input=  message.content,
            channel_id= str(message.channel.id),
            workflow=   "chat_agent",
        )

        # Show typing indicator
        async with message.channel.typing():
            response = await run_agent_full(**params)

        await message.reply(response)


adapter = DiscordAdapter()


def create_discord_client():
    """Initialize Discord client."""
    try:
        import discord
    except ImportError:
        raise ImportError("Install: pip install discord.py")

    intents         = discord.Intents.default()
    intents.message_content = True
    client          = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info(f"Discord bot connected: {client.user}")

    @client.event
    async def on_message(message):
        await adapter.handle(message)

    return client


async def run_discord_bot():
    """Run Discord bot (used when running standalone)."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("Missing DISCORD_BOT_TOKEN in .env")

    client = create_discord_client()
    await client.start(token)
