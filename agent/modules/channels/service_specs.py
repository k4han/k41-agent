from collections.abc import Callable
from dataclasses import dataclass

from agent.modules.channels.contracts import ChatChannelAdapter
from agent.modules.channels.manager import ChannelRunner


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    name: str
    runner_loader: Callable[[], ChannelRunner]
    required_env: tuple[str, ...] = ()
    adapter_loader: Callable[[], ChatChannelAdapter] | None = None


def load_telegram_runner() -> ChannelRunner:
    return load_telegram_adapter().create_runner()


def load_telegram_adapter() -> ChatChannelAdapter:
    from agent.modules.channels.telegram.adapter import get_telegram_adapter

    return get_telegram_adapter()


def load_discord_runner() -> ChannelRunner:
    return load_discord_adapter().create_runner()


def load_discord_adapter() -> ChatChannelAdapter:
    from agent.modules.channels.discord.adapter import get_discord_adapter

    return get_discord_adapter()


BUILTIN_CHANNEL_SPECS = (
    ChannelSpec(
        name="telegram",
        runner_loader=load_telegram_runner,
        required_env=("TELEGRAM_BOT_TOKEN",),
        adapter_loader=load_telegram_adapter,
    ),
    ChannelSpec(
        name="discord",
        runner_loader=load_discord_runner,
        required_env=("DISCORD_BOT_TOKEN",),
        adapter_loader=load_discord_adapter,
    ),
)
