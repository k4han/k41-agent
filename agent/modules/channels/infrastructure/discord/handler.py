import logging
import os

from agent.modules.agent_runtime.public import build_run_params, run_agent_full

logger = logging.getLogger(__name__)


async def handle_message(message) -> None:
    """Handle a Discord message."""

    if message.author.bot:
        return

    params = build_run_params(
        platform="discord",
        user_id=str(message.author.id),
        user_input=message.content,
        channel_id=str(message.channel.id),
        workflow="react_agent",
    )

    async with message.channel.typing():
        response = await run_agent_full(**params)

    await message.reply(response)


def create_discord_client():
    """Initialize the Discord client."""

    try:
        import discord
    except ImportError as exc:
        raise ImportError("Install: pip install discord.py") from exc

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info("Discord bot connected: %s", client.user)

    @client.event
    async def on_message(message):
        await handle_message(message)

    return client


async def run_discord_bot() -> None:
    """Run the Discord bot."""

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("Missing DISCORD_BOT_TOKEN in .env")

    client = create_discord_client()
    await client.start(token)
