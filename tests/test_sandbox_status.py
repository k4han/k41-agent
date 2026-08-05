"""Tests for Modal sandbox status reconciliation.

Modal's ``Sandbox.list`` never returns finished sandboxes (the SDK hardcodes
``include_finished=False``), so the dashboard has to probe each known sandbox
by id to learn that it auto-terminated (idle timeout / timeout). These tests
cover the probe helper and the merge logic that reconciles local
``thread_workspaces`` records with live cloud state.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agent.modules.workspaces import modal_backend, sandboxes
from agent.modules.workspaces.modal_backend import probe_modal_sandbox
from agent.modules.workspaces.sandboxes import _merge_sandbox_lists, list_sandboxes


def _async_return(value: Any):
    async def _fn(*args, **kwargs):
        return value

    return _fn


class _AioFn:
    """Mimics a Modal SDK function exposed with an ``.aio`` async variant."""

    def __init__(self, fn) -> None:
        self._fn = fn

    def __call__(self, *args, **kwargs):
        return self._fn(*args, **kwargs)

    async def aio(self, *args, **kwargs):
        result = self._fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result


def _thread_record(
    *,
    sandbox_id: str = "sb-1",
    status: str = "started",
    on_cloud: bool = True,
    thread_id: str | None = "t1",
) -> dict[str, Any]:
    return {
        "sandbox_id": sandbox_id,
        "backend": "modal",
        "label": f"modal:{sandbox_id}",
        "root": "/workspace",
        "status": status,
        "thread_id": thread_id,
        "repository_full_name": None,
        "last_used_at": None,
        "last_started_at": None,
        "last_stopped_at": None,
        "last_archived_at": None,
        "created_at": None,
        "updated_at": None,
        "on_cloud": on_cloud,
        "is_orphan": False,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# _merge_sandbox_lists
# ---------------------------------------------------------------------------


def test_merge_marks_thread_record_missing_from_cloud_as_stopped():
    thread = _thread_record(status="started")
    result = _merge_sandbox_lists(
        [thread],
        [],
        cloud_scanned=True,
        missing_from_cloud_status="stopped",
    )
    assert result[0]["status"] == "stopped"
    assert result[0]["on_cloud"] is False


def test_merge_keeps_live_status_when_confirmed_by_probe():
    thread = _thread_record(status="started")
    result = _merge_sandbox_lists(
        [thread],
        [],
        cloud_scanned=True,
        missing_from_cloud_status="stopped",
        probe_confirmed_ids={"sb-1"},
    )
    assert result[0]["status"] == "started"
    assert result[0]["on_cloud"] is True


def test_merge_does_not_downgrade_without_cloud_scan():
    thread = _thread_record(status="started")
    result = _merge_sandbox_lists([thread], [])
    assert result[0]["status"] == "started"
    assert result[0]["on_cloud"] is True


def test_merge_keeps_status_when_cloud_scan_failed():
    thread = _thread_record(status="started")
    result = _merge_sandbox_lists(
        [thread],
        [],
        cloud_scanned=True,
        cloud_scan_ok=False,
        missing_from_cloud_status="stopped",
    )
    assert result[0]["status"] == "started"
    assert result[0]["on_cloud"] is True


def test_merge_cloud_record_overrides_thread_status():
    thread = _thread_record(status="started")
    cloud = _thread_record(status="stopped", on_cloud=True, thread_id=None)
    result = _merge_sandbox_lists([thread], [cloud], cloud_scanned=True)
    assert result[0]["status"] == "stopped"


def test_merge_preserves_already_stopped_record_missing_from_cloud():
    thread = _thread_record(status="stopped", on_cloud=False)
    result = _merge_sandbox_lists(
        [thread],
        [],
        cloud_scanned=True,
        missing_from_cloud_status="stopped",
    )
    assert result[0]["status"] == "stopped"
    assert result[0]["on_cloud"] is False


# ---------------------------------------------------------------------------
# probe_modal_sandbox
# ---------------------------------------------------------------------------


def _install_modal_module(monkeypatch, *, sandbox: Any, from_id_fn=None) -> None:
    if from_id_fn is None:
        def from_id_fn(sandbox_id, client=None):
            return sandbox

    fake_module = SimpleNamespace(
        Sandbox=SimpleNamespace(from_id=_AioFn(from_id_fn)),
    )
    monkeypatch.setattr(modal_backend, "get_modal_module", lambda: fake_module)
    monkeypatch.setattr(modal_backend, "get_modal_client", _async_return(object()))


@pytest.mark.asyncio
async def test_probe_modal_sandbox_started(monkeypatch):
    sandbox = SimpleNamespace(returncode=None, poll=_AioFn(lambda: None))
    _install_modal_module(monkeypatch, sandbox=sandbox)
    assert await probe_modal_sandbox("sb-1") == {"status": "started", "on_cloud": True}


@pytest.mark.asyncio
async def test_probe_modal_sandbox_stopped(monkeypatch):
    sandbox = SimpleNamespace(returncode=137, poll=_AioFn(lambda: 137))
    _install_modal_module(monkeypatch, sandbox=sandbox)
    assert await probe_modal_sandbox("sb-1") == {"status": "stopped", "on_cloud": True}


@pytest.mark.asyncio
async def test_probe_modal_sandbox_stopped_via_poll(monkeypatch):
    # returncode is None initially; poll() reveals the sandbox has finished.
    sandbox = SimpleNamespace(returncode=None, poll=_AioFn(lambda: 0))
    _install_modal_module(monkeypatch, sandbox=sandbox)
    assert await probe_modal_sandbox("sb-1") == {"status": "stopped", "on_cloud": True}


@pytest.mark.asyncio
async def test_probe_modal_sandbox_destroyed_when_not_found(monkeypatch):
    def raise_not_found(sandbox_id, client=None):
        raise RuntimeError(f"Sandbox {sandbox_id} not found")

    _install_modal_module(monkeypatch, sandbox=None, from_id_fn=raise_not_found)
    assert await probe_modal_sandbox("sb-1") == {
        "status": "destroyed",
        "on_cloud": False,
    }


@pytest.mark.asyncio
async def test_probe_modal_sandbox_transient_error_returns_none(monkeypatch):
    def raise_transient(sandbox_id, client=None):
        raise RuntimeError("transient network blip")

    _install_modal_module(monkeypatch, sandbox=None, from_id_fn=raise_transient)
    assert await probe_modal_sandbox("sb-1") is None


@pytest.mark.asyncio
async def test_probe_modal_sandbox_client_unavailable_returns_none(monkeypatch):
    async def fail_client():
        raise ValueError("Modal workspace backend is disabled.")

    monkeypatch.setattr(modal_backend, "get_modal_client", fail_client)
    assert await probe_modal_sandbox("sb-1") is None


# ---------------------------------------------------------------------------
# list_sandboxes (modal reconciliation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_skips_final_statuses(monkeypatch):
    called: list[str] = []
    fake_descriptor = SimpleNamespace(status_probe_loader="fake:probe")
    fake_registry = SimpleNamespace(
        require=lambda backend: fake_descriptor,
        resolve_loader=lambda backend, loader: (lambda sandbox_id: called.append(sandbox_id)),
    )
    monkeypatch.setattr(
        sandboxes, "get_workspace_backend_registry", lambda: fake_registry
    )
    started = _thread_record(sandbox_id="sb-start", status="started")
    stopped = _thread_record(sandbox_id="sb-stop", status="stopped")
    result = await sandboxes._probe_sandbox_statuses("modal", [started, stopped])
    assert called == ["sb-start"]
    assert result == {"sb-stop"}


@pytest.mark.asyncio
async def test_list_sandboxes_modal_probes_and_flips_running_to_stopped(monkeypatch):
    thread = _thread_record(status="started")

    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_thread_records", _async_return([thread])
    )
    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_cloud", _async_return(([], True))
    )

    async def fake_probe(backend, records):
        assert backend == "modal"
        for record in records:
            record["status"] = "stopped"
            record["on_cloud"] = False
        return {record["sandbox_id"] for record in records}

    monkeypatch.setattr(sandboxes, "_probe_sandbox_statuses", fake_probe)

    result = await list_sandboxes("modal", include_all=False)
    assert result["sandboxes"][0]["status"] == "stopped"
    assert result["sandboxes"][0]["on_cloud"] is False


@pytest.mark.asyncio
async def test_list_sandboxes_modal_marks_missing_from_cloud_when_probe_silent(monkeypatch):
    # The probe cannot confirm anything (e.g. transient error -> empty set) but
    # the cloud scan succeeds and does not contain the sandbox: a running
    # sandbox would appear in the scan, so the stale "running" record can be
    # safely downgraded to stopped/off-cloud.
    thread = _thread_record(status="started")

    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_thread_records", _async_return([thread])
    )
    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_cloud", _async_return(([], True))
    )

    async def silent_probe(backend, records):
        return set()

    monkeypatch.setattr(sandboxes, "_probe_sandbox_statuses", silent_probe)

    result = await list_sandboxes("modal", include_all=True)
    assert result["sandboxes"][0]["status"] == "stopped"
    assert result["sandboxes"][0]["on_cloud"] is False


@pytest.mark.asyncio
async def test_list_sandboxes_modal_keeps_status_when_cloud_scan_fails(monkeypatch):
    # Provider outage: both the probe and the cloud scan fail, so absence from
    # the scan proves nothing. The stored "started" status must be kept.
    thread = _thread_record(status="started")

    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_thread_records", _async_return([thread])
    )
    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_cloud", _async_return(([], False))
    )

    async def silent_probe(backend, records):
        return set()

    monkeypatch.setattr(sandboxes, "_probe_sandbox_statuses", silent_probe)

    result = await list_sandboxes("modal", include_all=True)
    assert result["sandboxes"][0]["status"] == "started"
    assert result["sandboxes"][0]["on_cloud"] is True


@pytest.mark.asyncio
async def test_list_sandboxes_modal_keeps_running_when_probe_confirms(monkeypatch):
    thread = _thread_record(status="started")

    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_thread_records", _async_return([thread])
    )
    monkeypatch.setattr(
        sandboxes, "_modal_sandboxes_from_cloud", _async_return(([], True))
    )

    async def confirming_probe(backend, records):
        return {record["sandbox_id"] for record in records}

    monkeypatch.setattr(sandboxes, "_probe_sandbox_statuses", confirming_probe)

    result = await list_sandboxes("modal", include_all=True)
    assert result["sandboxes"][0]["status"] == "started"
    assert result["sandboxes"][0]["on_cloud"] is True
