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
        port=4141,
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


@pytest.mark.asyncio
async def test_shutdown_without_startup_preserves_active_container(tmp_path):
    from agent.bootstrap.container import (
        clear_active_container,
        create_test_container,
        get_active_container,
    )

    active = create_test_container(tmp_path=tmp_path / "active")
    runtime_container = create_test_container(tmp_path=tmp_path / "runtime")
    previous = active.activate()
    try:
        runtime = AppRuntime(container=runtime_container)
        await runtime.shutdown()

        assert get_active_container() is active
        assert runtime._started is False
    finally:
        active.deactivate(previous)
        clear_active_container()


@pytest.mark.asyncio
async def test_startup_failure_restores_previous_active_container(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    from agent.bootstrap.container import (
        clear_active_container,
        create_test_container,
        get_active_container,
    )

    active = create_test_container(tmp_path=tmp_path / "active")
    runtime_container = create_test_container(tmp_path=tmp_path / "runtime")

    async def failing_persistence():
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(
        runtime_container, "initialize_persistence", failing_persistence
    )

    previous = active.activate()
    try:
        runtime = AppRuntime(container=runtime_container)
        with pytest.raises(RuntimeError, match="persistence failed"):
            await runtime.startup()

        assert get_active_container() is active
        assert runtime._started is False
        assert runtime._activated is False
    finally:
        active.deactivate(previous)
        clear_active_container()


@pytest.mark.asyncio
async def test_shutdown_continues_after_step_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A failing step must not skip cleanup of the remaining ones."""
    import agent.modules.scheduler as scheduler_module
    import agent.modules.workspaces as workspaces_module

    from agent.bootstrap.container import (
        clear_active_container,
        create_test_container,
        get_active_container,
    )

    holder = create_test_container(tmp_path=tmp_path / "holder")
    runtime_container = create_test_container(tmp_path=tmp_path / "runtime")

    calls: list[str] = []

    class FakeChannelManager:
        def names(self):
            return ["telegram"]

    async def failing_channels(manager):
        calls.append("channels")
        raise RuntimeError("channels failed")

    async def ok_scheduler(container=None):
        calls.append("scheduler")

    async def ok_workspace_services():
        calls.append("workspace")

    class FakeChatStreamManager:
        async def shutdown(self):
            calls.append("chat")

    async def ok_close_persistence():
        calls.append("persistence")

    monkeypatch.setattr(runtime_module, "stop_all_channels", failing_channels)
    monkeypatch.setattr(scheduler_module, "stop_scheduler", ok_scheduler)
    monkeypatch.setattr(
        workspaces_module, "stop_workspace_background_services", ok_workspace_services
    )
    monkeypatch.setattr(runtime_container, "_channel_manager", FakeChannelManager())
    monkeypatch.setattr(runtime_container, "_chat_stream_manager", FakeChatStreamManager())
    monkeypatch.setattr(
        runtime_container, "close_persistence", ok_close_persistence
    )
    runtime_container._persistence_ready = True

    runtime = AppRuntime(container=runtime_container)
    previous = holder.activate()
    try:
        runtime._previous_active = runtime_container.activate()
        runtime._activated = True
        runtime._started = True

        await runtime.shutdown()

        assert calls == ["channels", "scheduler", "chat", "workspace", "persistence"]
        assert runtime._started is False
        assert runtime._activated is False
        assert get_active_container() is holder
    finally:
        holder.deactivate(previous)
        clear_active_container()


@pytest.mark.asyncio
async def test_shutdown_finishes_chat_cleanup_before_workspace_and_persistence(isolated_container, monkeypatch):
    calls = []
    started = asyncio.Event()
    isolated_container._persistence_ready = True

    async def run(**params):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            assert isolated_container._persistence_ready
            calls.append("run-cleaned")
        yield  # pragma: no cover

    async def record_error(thread_id, event):
        assert isolated_container._persistence_ready
        calls.append("error-saved")

    async def stop_producers():
        pass

    async def stop_workspace():
        assert session.task.done()
        assert session._event_log.closed
        calls.append("workspace")

    async def close_persistence():
        assert session.task.done()
        calls.append("persistence")
        isolated_container._persistence_ready = False

    runtime = AppRuntime(container=isolated_container)
    runtime._started = True
    monkeypatch.setattr("agent.modules.conversations.record_thread_run_error", record_error)
    monkeypatch.setattr(runtime, "_stop_channels_step", stop_producers)
    monkeypatch.setattr(runtime, "_stop_scheduler_step", stop_producers)
    monkeypatch.setattr(runtime, "_stop_workspace_services_step", stop_workspace)
    monkeypatch.setattr(isolated_container, "close_persistence", close_persistence)
    session = await isolated_container.chat_stream_manager.get_or_create_session("shutdown-order", {}, run)
    await started.wait()
    await asyncio.wait_for(runtime.shutdown(), timeout=1)

    assert calls == ["run-cleaned", "error-saved", "workspace", "persistence"]
    assert await isolated_container.chat_stream_manager.get_session(session.thread_id) is None
