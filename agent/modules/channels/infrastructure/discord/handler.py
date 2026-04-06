import logging

from agent.modules.agent_runtime.public import build_run_params, run_agent_full
from agent.modules.agents.public import resolve_catalog_agent_name
from agent.modules.users.application.pairing_handler import authenticate_channel_message
from agent.modules.users.domain.constants import Platform
from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

logger = logging.getLogger(__name__)


def create_discord_client():
    """Initialize the Discord client."""

    try:
        import discord
    except ImportError as exc:
        raise ImportError("Install: pip install discord.py") from exc

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    # Cache config service and agent name once at client creation
    config = get_config_service()
    default_agent_name = resolve_catalog_agent_name(
        config.get_str("channels.discord.default_agent", ""),
        "default-agent",
        "default",
    )

    @client.event
    async def on_ready():
        logger.info("Discord bot connected: %s", client.user)

    @client.event
    async def on_message(message):
        if message.author.bot:
            return

        user_id = str(message.author.id)

        if not await authenticate_channel_message(Platform.DISCORD, user_id, message.content, message.reply):
            return

        params = build_run_params(
            platform=Platform.DISCORD,
            user_id=user_id,
            user_input=message.content,
            channel_id=str(message.channel.id),
            agent_name=default_agent_name,
        )

        async with message.channel.typing():
            response = await run_agent_full(**params)

        await message.reply(response)

    return client


async def run_discord_bot() -> None:
    """Run the Discord bot."""

    config = get_config_service()
    token = config.get_str("channels.discord.bot_token", "")

    if is_placeholder_value(token):
        raise ValueError(
            "Discord bot token not configured. "
            "Set 'channels.discord.bot_token' in ~/.kaka-agent/config.yaml"
        )

    client = create_discord_client()
    await client.start(token)
