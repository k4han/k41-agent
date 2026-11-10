"""Opt-in end-to-end check against the real Instabox API.

Run with ``INSTABOX_LIVE=1 INSTABOX_API_KEY=ibk_... uv run pytest tests/test_instabox_live.py``.
The test creates real machines and a snapshot and removes them afterwards.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

from agent.modules.instabox import InstaboxService, InstaboxSettings

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(1800),
    pytest.mark.skipif(os.getenv("INSTABOX_LIVE") != "1", reason="Set INSTABOX_LIVE=1 to test the real Instabox API"),
]


async def _wait_snapshot_ready(service: InstaboxService, snapshot_id: str, timeout: float = 900.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = (await service.get_snapshot(snapshot_id)).get("state")
        if state == "ready":
            return
        if state == "failed":
            raise AssertionError(f"Snapshot {snapshot_id} failed.")
        await asyncio.sleep(10)
    raise AssertionError(f"Snapshot {snapshot_id} was not ready in time.")


@pytest.mark.asyncio
async def test_instabox_machine_lifecycle(isolated_container):
    api_key = os.getenv("INSTABOX_API_KEY", "")
    if not api_key:
        pytest.skip("Set INSTABOX_API_KEY to an ibk_ key with machines and images scopes.")
    settings = InstaboxSettings(
        enabled=True,
        api_key=api_key,
        api_url=os.getenv("INSTABOX_API_URL") or "https://api.instabox.sh",
        template=os.getenv("INSTABOX_TEMPLATE") or "agent-browser",
    )
    from agent.shared.infrastructure.db import Base, load_orm_models
    from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata, container=isolated_container)
    service = InstaboxService(isolated_container, settings_loader=lambda: settings)
    suffix = uuid.uuid4().hex[:8]
    machines: list[str] = []
    snapshot_id = None
    try:
        created = await service.create_machine(name=f"k41-live-{suffix}", wait=True)
        assert created["status"] == "succeeded", created
        machine_id = created["machine_id"]
        machines.append(machine_id)

        client = await service.client()
        result = await client.exec(machine_id, cmd="echo k41-live && python3 --version", timeout_seconds=60)
        assert result["exitCode"] == 0 and "k41-live" in result["stdout"]

        await client.exec(
            machine_id,
            cmd="nohup python3 -m http.server 8765 >/tmp/k41-http.log 2>&1 &",
            timeout_seconds=30,
        )
        published = await service.publish_port(machine_id, 8765)
        assert "token" not in published
        assert "_token=" in published["link"]["url"]
        await service.close_port(machine_id, 8765)

        await service.desktop.ensure_started(machine_id)
        image = await service.desktop.screenshot(machine_id)
        assert image[:8] == b"\x89PNG\r\n\x1a\n"

        snapshot = await service.create_snapshot(machine_id, name=f"k41-live-{suffix}")
        snapshot_id = snapshot["operation_id"]
        await _wait_snapshot_ready(service, snapshot_id)

        forked = await service.fork_machine(machine_id, name=f"k41-fork-{suffix}", wait=True)
        assert forked["status"] == "succeeded", forked
        machines.append(forked["machine_id"])

        restored = await service.machine_action(machine_id, "restore", body={"snapshotId": snapshot_id}, wait=True)
        assert restored["status"] == "succeeded", restored
        assert (await service.get_machine(machine_id))["state"] == "running"
    finally:
        for machine_id in machines:
            try:
                await service.delete_machine(machine_id, confirm=machine_id, wait=True)
            except Exception as exc:  # pragma: no cover - cleanup best effort
                print(f"Failed to delete Instabox machine {machine_id}: {exc}")
        if snapshot_id:
            try:
                await service.delete_snapshot(snapshot_id, confirm=snapshot_id)
            except Exception as exc:  # pragma: no cover - cleanup best effort
                print(f"Failed to delete Instabox snapshot {snapshot_id}: {exc}")
        await service.close()
        await close_async_engine(container=isolated_container)
