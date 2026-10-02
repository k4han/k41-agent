"""CLIRuntime lifecycle: startup failure cleanup and shutdown guards."""

import pytest

from agent.bootstrap.container import (
    clear_active_container,
    create_test_container,
    get_active_container,
)
from agent.delivery.cli.runtime import CLIRuntime


@pytest.mark.asyncio
async def test_shutdown_without_startup_preserves_active_container(tmp_path):
    active = create_test_container(tmp_path=tmp_path / "active")
    runtime_container = create_test_container(tmp_path=tmp_path / "runtime")
    previous = active.activate()
    try:
        runtime = CLIRuntime(container=runtime_container)
        await runtime.shutdown()

        assert get_active_container() is active
        assert runtime._started is False
        assert runtime._activated is False
    finally:
        active.deactivate(previous)
        clear_active_container()


@pytest.mark.asyncio
async def test_startup_failure_cleans_up_and_restores_active_container(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    active = create_test_container(tmp_path=tmp_path / "active")
    runtime_container = create_test_container(tmp_path=tmp_path / "runtime")

    async def failing_persistence():
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(
        runtime_container, "initialize_persistence", failing_persistence
    )

    previous = active.activate()
    try:
        runtime = CLIRuntime(container=runtime_container)
        with pytest.raises(RuntimeError, match="persistence failed"):
            await runtime.startup()

        assert get_active_container() is active
        assert runtime._started is False
        assert runtime._activated is False
        assert runtime._previous_active is None
    finally:
        active.deactivate(previous)
        clear_active_container()


@pytest.mark.asyncio
async def test_shutdown_continues_after_scheduler_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A failing scheduler stop must not skip closing persistence."""
    import agent.modules.scheduler as scheduler_module

    holder = create_test_container(tmp_path=tmp_path / "active")
    runtime_container = create_test_container(tmp_path=tmp_path / "runtime")

    calls: list[str] = []

    async def failing_scheduler(container=None):
        calls.append("scheduler")
        raise RuntimeError("scheduler failed")

    async def ok_close_persistence():
        calls.append("persistence")

    monkeypatch.setattr(scheduler_module, "stop_scheduler", failing_scheduler)
    monkeypatch.setattr(
        runtime_container, "close_persistence", ok_close_persistence
    )

    previous = holder.activate()
    try:
        runtime = CLIRuntime(container=runtime_container)
        runtime._previous_active = runtime_container.activate()
        runtime._activated = True
        runtime._started = True

        await runtime.shutdown()

        assert calls == ["scheduler", "persistence"]
        assert runtime._started is False
        assert runtime._activated is False
        assert get_active_container() is holder
    finally:
        holder.deactivate(previous)
        clear_active_container()


@pytest.mark.asyncio
async def test_startup_success_then_shutdown_restores_previous_active(tmp_path):
    active = create_test_container(tmp_path=tmp_path / "active")
    runtime_container = create_test_container(tmp_path=tmp_path / "runtime")
    previous = active.activate()
    try:
        runtime = CLIRuntime(container=runtime_container)
        await runtime.startup()

        assert runtime._started is True
        assert get_active_container() is runtime_container

        await runtime.shutdown()

        assert runtime._started is False
        assert runtime._activated is False
        assert get_active_container() is active
    finally:
        active.deactivate(previous)
        clear_active_container()
        await runtime_container.close_persistence()
