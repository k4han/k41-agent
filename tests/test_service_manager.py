import asyncio

import pytest

from agent.services import ServiceManager, ServiceStatus


async def wait_for_status(
    manager: ServiceManager,
    name: str,
    expected: ServiceStatus,
    attempts: int = 50,
) -> None:
    for _ in range(attempts):
        if manager.status(name)["status"] == expected:
            return
        await asyncio.sleep(0.01)

    raise AssertionError(f"Service '{name}' did not reach status '{expected}'.")


@pytest.mark.asyncio
async def test_service_manager_tracks_running_and_stopped_status():
    manager = ServiceManager()
    started = asyncio.Event()

    async def runner():
        started.set()
        await asyncio.Event().wait()

    manager.register("telegram", runner)

    await manager.start("telegram")
    await asyncio.wait_for(started.wait(), timeout=1)
    await wait_for_status(manager, "telegram", ServiceStatus.RUNNING)

    assert manager.status("telegram")["status"] == ServiceStatus.RUNNING

    await manager.stop("telegram")

    assert manager.status("telegram")["status"] == ServiceStatus.STOPPED


@pytest.mark.asyncio
async def test_service_manager_start_is_idempotent_while_active():
    manager = ServiceManager()
    started = asyncio.Event()
    calls = 0

    async def runner():
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Event().wait()

    manager.register("telegram", runner)

    await manager.start("telegram")
    await manager.start("telegram")

    await asyncio.wait_for(started.wait(), timeout=1)
    await wait_for_status(manager, "telegram", ServiceStatus.RUNNING)

    assert calls == 1

    await manager.stop("telegram")


@pytest.mark.asyncio
async def test_service_manager_stop_recovers_from_error_state():
    manager = ServiceManager()

    async def runner():
        raise RuntimeError("boom")

    manager.register("discord", runner)

    await manager.start("discord")
    await wait_for_status(manager, "discord", ServiceStatus.ERROR)

    status = manager.status("discord")
    assert status["status"] == ServiceStatus.ERROR
    assert status["error"] == "boom"

    await manager.stop("discord")

    status = manager.status("discord")
    assert status["status"] == ServiceStatus.STOPPED
    assert status["error"] == "boom"
