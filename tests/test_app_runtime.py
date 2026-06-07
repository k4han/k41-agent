import asyncio

import pytest

import agent.bootstrap.runtime as runtime_module
from agent.bootstrap.runtime import AppRuntime, ChannelDescriptor
from agent.modules.channels import service as channel_service
from agent.bootstrap.settings import BootstrapConfig
from agent.modules.channels import ChannelStatus
from agent.shared.config import RuntimeSettings


@pytest.fixture(autouse=True)
def restore_builtin_channel_descriptors():
    yield
    from agent.modules.channels.registry import get_channel_registry
    from agent.modules.channels.service_specs import BUILTIN_CHANNEL_DESCRIPTORS

    registry = get_channel_registry()
    for descriptor in BUILTIN_CHANNEL_DESCRIPTORS:
        registry.register_descriptor(descriptor, replace=True)
        registry.unregister(descriptor.name)
    registry._lazy.clear_instances()


async def wait_for_status(runtime: AppRuntime, name: str, expected: ChannelStatus) -> None:
    for _ in range(50):
        if runtime.channel_manager.status(name)["status"] == expected:
            return
        await asyncio.sleep(0.01)

    raise AssertionError(f"Channel '{name}' did not reach status '{expected}'.")


def build_bootstrap_config() -> BootstrapConfig:
    return BootstrapConfig(
        host="0.0.0.0",
        port=8000,
        enable_web=True,
        enable_api=True,
        enable_dashboard=True,
    )


def build_runtime_settings(channel_enabled: dict[str, bool]) -> RuntimeSettings:
    return RuntimeSettings(channel_enabled=channel_enabled)


def build_runtime(channel_enabled: dict[str, bool]) -> AppRuntime:
    return AppRuntime(
        build_bootstrap_config(),
        build_runtime_settings(channel_enabled),
    )


def build_runner(started_event: asyncio.Event):
    async def runner():
        started_event.set()
        await asyncio.Event().wait()

    return runner


class FakeChannelAdapter:
    def __init__(self, runner):
        self._runner = runner

    def create_runner(self):
        return self._runner


def build_channel_descriptor(name: str) -> ChannelDescriptor:
    return ChannelDescriptor(
        kind="channel",
        name=name,
        title=name.title(),
        config_prefix=f"channels.{name}",
        loader="",
        has_runner=True,
    )


def test_runtime_registers_all_channels_even_when_boot_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    telegram_started = asyncio.Event()
    discord_started = asyncio.Event()

    monkeypatch.setattr(
        runtime_module,
        "BUILTIN_CHANNEL_DESCRIPTORS",
        (
            build_channel_descriptor("telegram"),
            build_channel_descriptor("discord"),
        ),
    )
    monkeypatch.setattr(
        channel_service,
        "load_channel_adapter",
        lambda name: FakeChannelAdapter(
            build_runner(telegram_started if name == "telegram" else discord_started)
        ),
    )

    runtime = build_runtime({"telegram": True, "discord": False})
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
        "BUILTIN_CHANNEL_DESCRIPTORS",
        (
            build_channel_descriptor("telegram"),
            build_channel_descriptor("discord"),
        ),
    )
    monkeypatch.setattr(
        channel_service,
        "load_channel_adapter",
        lambda name: FakeChannelAdapter(
            build_runner(telegram_started if name == "telegram" else discord_started)
        ),
    )

    runtime = build_runtime({"telegram": True, "discord": False})
    runtime._register_channels()
    await runtime._start_enabled_channels()

    await asyncio.wait_for(telegram_started.wait(), timeout=1)
    await wait_for_status(runtime, "telegram", ChannelStatus.RUNNING)

    assert discord_started.is_set() is False
    assert runtime.channel_manager.status("discord")["status"] == ChannelStatus.STOPPED

    await runtime.channel_manager.stop_all()
