import logging

from agent.modules.channels.application.channel_manager import ChannelManager
from agent.modules.channels.infrastructure.service_specs import (
    BUILTIN_CHANNEL_SPECS,
    ChannelSpec,
)
from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

logger = logging.getLogger(__name__)


def register_channels(
    channel_manager: ChannelManager,
    specs: tuple[ChannelSpec, ...],
) -> None:
    for spec in specs:
        channel_manager.register(spec.name, spec.runner_loader())
        logger.info("Channel registered: %s", spec.name)


def register_builtin_channels(
    channel_manager: ChannelManager,
    specs: tuple[ChannelSpec, ...] | None = None,
) -> None:
    if specs is None:
        specs = BUILTIN_CHANNEL_SPECS
    register_channels(channel_manager, specs)


async def start_enabled_channels(
    channel_manager: ChannelManager,
    boot_flags: dict[str, bool],
    specs: tuple[ChannelSpec, ...] | None = None,
) -> None:
    if specs is None:
        specs = BUILTIN_CHANNEL_SPECS
    channels_to_start: list[str] = []

    config = get_config_service()

    for spec in specs:
        if not boot_flags.get(spec.name, True):
            logger.info("Channel starts disabled by config: %s", spec.name)
            continue

        # Check if required config keys are present
        missing_keys = []
        for env_name in spec.required_env:
            config_key = _ENV_TO_CONFIG_MAP.get(env_name)
            if not config_key:
                logger.warning("Unknown env var '%s' for channel '%s'", env_name, spec.name)
                continue

            value = config.get_str(config_key, "")
            if is_placeholder_value(value):
                missing_keys.append(config_key)

        if missing_keys:
            logger.warning(
                "Channel '%s' is configured to start but required config keys are missing: %s",
                spec.name,
                ", ".join(missing_keys),
            )
            continue

        channels_to_start.append(spec.name)

    if not channels_to_start:
        logger.info("No background channels configured to start on boot.")
        return

    logger.info(
        "Starting configured background channels: %s",
        ", ".join(channels_to_start),
    )
    await channel_manager.start_many(channels_to_start)


_ENV_TO_CONFIG_MAP = {
    "TELEGRAM_BOT_TOKEN": "channels.telegram.bot_token",
    "DISCORD_BOT_TOKEN": "channels.discord.bot_token",
}


async def start_channel(channel_manager: ChannelManager, name: str) -> dict[str, str | None]:
    await channel_manager.start(name)
    return channel_manager.status(name)


async def stop_channel(channel_manager: ChannelManager, name: str) -> dict[str, str | None]:
    await channel_manager.stop(name)
    return channel_manager.status(name)


async def start_all_channels(
    channel_manager: ChannelManager,
) -> list[dict[str, str | None]]:
    await channel_manager.start_all()
    return channel_manager.status_all()


async def stop_all_channels(
    channel_manager: ChannelManager,
) -> list[dict[str, str | None]]:
    await channel_manager.stop_all()
    return channel_manager.status_all()


def list_channel_statuses(channel_manager: ChannelManager) -> list[dict[str, str | None]]:
    return channel_manager.status_all()


def get_channel_status(
    channel_manager: ChannelManager,
    name: str,
) -> dict[str, str | None]:
    return channel_manager.status(name)
