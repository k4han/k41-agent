from collections.abc import Callable
from dataclasses import dataclass

from agent.modules.channels.application.channel_manager import ChannelRunner


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    name: str
    runner_loader: Callable[[], ChannelRunner]
    required_env: tuple[str, ...] = ()


def load_telegram_runner() -> ChannelRunner:
    from agent.modules.channels.infrastructure.telegram.handler import run_telegram_bot

    return run_telegram_bot


def load_discord_runner() -> ChannelRunner:
    from agent.modules.channels.infrastructure.discord.handler import run_discord_bot

    return run_discord_bot


BUILTIN_CHANNEL_SPECS = (
    ChannelSpec(
        name="telegram",
        runner_loader=load_telegram_runner,
        required_env=("TELEGRAM_BOT_TOKEN",),
    ),
    ChannelSpec(
        name="discord",
        runner_loader=load_discord_runner,
        required_env=("DISCORD_BOT_TOKEN",),
    ),
)
