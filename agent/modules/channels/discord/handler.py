import logging

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value
from agent.modules.channels.discord.adapter import (
    get_discord_adapter,
    handle_discord_message,
)

logger = logging.getLogger(__name__)


def create_discord_client():
    """Initialize the Discord client."""

    try:
        import discord
    except ImportError as exc:
        raise ImportError("Install discord.py with uv before enabling Discord.") from exc

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info("Discord bot connected: %s", client.user)

    @client.event
    async def on_message(message):
        await handle_discord_message(message)

    return client


async def run_discord_bot() -> None:
    """Run the Discord bot."""

    config = get_config_service()
    token = config.get_str("channels.discord.bot_token", "")

    if is_placeholder_value(token):
        raise ValueError(
            "Discord bot token not configured. "
            "Set 'channels.discord.bot_token' in the dashboard channel settings."
        )

    client = create_discord_client()
    adapter = get_discord_adapter()
    adapter.set_client(client)
    try:
        await client.start(token)
    finally:
        adapter.set_client(None)
