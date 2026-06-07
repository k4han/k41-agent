from __future__ import annotations

import logging
from collections.abc import Callable

from agent.modules.channels.contracts import ChatChannelAdapter
from agent.modules.channels.manager import ChannelManager, ChannelRunner
from agent.modules.channels.service_specs import (
    BUILTIN_CHANNEL_DESCRIPTORS,
    ChannelDescriptor,
)
from agent.modules.channels.registry import (
    get_channel_registry,
    list_channel_catalog,
    register_channel_descriptors,
)
from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

logger = logging.getLogger(__name__)


def register_channels(
    channel_manager: ChannelManager,
    descriptors: tuple[ChannelDescriptor, ...],
) -> None:
    """Register runner loaders for each descriptor with a runner.

    Builtin descriptors are auto-registered in the channel registry the first
    time it is accessed. Custom descriptors should be registered via
    ``register_channel_descriptors`` before being passed here.
    """
    for descriptor in descriptors:
        if not descriptor.has_runner:
            continue
        if descriptor.name in channel_manager.names():
            continue
        channel_manager.register_loader(
            descriptor.name,
            _runner_loader_for_descriptor(descriptor),
        )
        logger.info("Channel registered: %s", descriptor.name)


def register_builtin_channel_adapters(
    descriptors: tuple[ChannelDescriptor, ...] | None = None,
) -> None:
    if descriptors is None:
        descriptors = BUILTIN_CHANNEL_DESCRIPTORS
    register_channel_descriptors(descriptors, replace=True)


def register_builtin_channels(
    channel_manager: ChannelManager,
    descriptors: tuple[ChannelDescriptor, ...] | None = None,
) -> None:
    if descriptors is None:
        descriptors = BUILTIN_CHANNEL_DESCRIPTORS
    register_channels(channel_manager, descriptors)


def load_channel_adapter(name: str) -> ChatChannelAdapter:
    return get_channel_registry().load_adapter(name)


def _runner_loader_for_descriptor(
    descriptor: ChannelDescriptor,
) -> Callable[[], ChannelRunner]:
    def load_runner() -> ChannelRunner:
        adapter = load_channel_adapter(descriptor.name)
        return adapter.create_runner()

    return load_runner


async def start_enabled_channels(
    channel_manager: ChannelManager,
    boot_flags: dict[str, bool],
    descriptors: tuple[ChannelDescriptor, ...] | None = None,
) -> None:
    if descriptors is None:
        descriptors = BUILTIN_CHANNEL_DESCRIPTORS
    channels_to_start: list[str] = []

    config = get_config_service()

    for descriptor in descriptors:
        if not descriptor.has_runner:
            continue
        if not boot_flags.get(descriptor.name, True):
            logger.info("Channel starts disabled by config: %s", descriptor.name)
            continue

        missing_keys = []
        for config_key in _required_config_keys(descriptor):
            value = config.get_str(config_key, "")
            if is_placeholder_value(value):
                missing_keys.append(config_key)

        if missing_keys:
            logger.warning(
                "Channel '%s' is configured to start but required config keys are missing: %s",
                descriptor.name,
                ", ".join(missing_keys),
            )
            continue

        channels_to_start.append(descriptor.name)

    if not channels_to_start:
        logger.info("No background channels configured to start on boot.")
        return

    logger.info(
        "Starting configured background channels: %s",
        ", ".join(channels_to_start),
    )
    await channel_manager.start_many(channels_to_start)


def _required_config_keys(descriptor: ChannelDescriptor) -> list[str]:
    return [
        field.config_key(descriptor.name)
        for field in descriptor.settings_schema
        if getattr(field, "required", False)
    ]


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


def get_registered_channel_catalog() -> list[dict[str, object]]:
    register_builtin_channel_adapters()
    return list_channel_catalog()


def get_channel_webhook_runtime(name: str) -> object | None:
    normalized = name.strip().lower()
    if normalized == "telegram":
        from agent.modules.channels.telegram.bot import get_telegram_webhook_runtime

        return get_telegram_webhook_runtime()
    return None
