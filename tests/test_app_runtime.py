import asyncio

import pytest

import agent.bootstrap.runtime as runtime_module
from agent.bootstrap.runtime import AppRuntime, ChannelSpec
from agent.bootstrap.settings import AppSettings
from agent.modules.channels.public import ChannelStatus


async def wait_for_status(runtime: AppRuntime, name: str, expected: ChannelStatus) -> None:
    for _ in range(50):
        if runtime.channel_manager.status(name)["status"] == expected:
            return
        await asyncio.sleep(0.01)

    raise AssertionError(f"Channel '{name}' did not reach status '{expected}'.")


def build_settings(service_boot_flags: dict[str, bool]) -> AppSettings:
    return AppSettings(
        host="0.0.0.0",
        port=8000,
        enable_web=True,
        enable_api=True,
        enable_dashboard=True,
        service_boot_flags=service_boot_flags,
    )


def build_runner(started_event: asyncio.Event):
    async def runner():
        started_event.set()
        await asyncio.Event().wait()

    return runner


def test_runtime_registers_all_channels_even_when_boot_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    telegram_started = asyncio.Event()
    discord_started = asyncio.Event()

    monkeypatch.setattr(
        runtime_module,
        "BUILTIN_CHANNEL_SPECS",
        (
            ChannelSpec("telegram", lambda: build_runner(telegram_started)),
            ChannelSpec("discord", lambda: build_runner(discord_started)),
        ),
    )

    runtime = AppRuntime(build_settings({"telegram": True, "discord": False}))
    runtime._register_channels()

    assert runtime.channel_manager.names() == ["telegram", "discord"]


@pytest.mark.asyncio
async def test_runtime_starts_only_channels_enabled_for_boot(
    monkeypatch: pytest.MonkeyPatch,
):
    telegram_started = asyncio.Event()
    discord_started = asyncio.Event()

    monkeypatch.setattr(
        runtime_module,
        "BUILTIN_CHANNEL_SPECS",
        (
            ChannelSpec("telegram", lambda: build_runner(telegram_started)),
            ChannelSpec("discord", lambda: build_runner(discord_started)),
        ),
    )

    runtime = AppRuntime(build_settings({"telegram": True, "discord": False}))
    runtime._register_channels()
    await runtime._start_enabled_channels()

    await asyncio.wait_for(telegram_started.wait(), timeout=1)
    await wait_for_status(runtime, "telegram", ChannelStatus.RUNNING)

    assert discord_started.is_set() is False
    assert runtime.channel_manager.status("discord")["status"] == ChannelStatus.STOPPED

    await runtime.channel_manager.stop_all()
