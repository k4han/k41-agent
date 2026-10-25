"""Host mirror reconciliation, process writes and portable monitor recovery."""

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import struct
import tempfile
from types import SimpleNamespace

import pytest

from agent.modules.projects import snapshot_worker
from agent.modules.projects.local_sync import _capture
from agent.modules.projects.storage import LocalProjectStorage
from agent.modules.projects.transfer import WorkspaceSnapshotTransfer
from agent.modules.projects.workspaces import ProjectWorkspaceError
from agent.modules.workspaces.filesystem_monitor import FilesystemMonitor
from agent.modules.workspaces.host_mirror import HostMirrorService, resolution_token, workspace_runtime_status
from agent.modules.workspaces.refs import WorkspaceRef


@pytest.fixture
def mirror(tmp_path):
    store = LocalProjectStorage(tmp_path / "storage")
    transfer = WorkspaceSnapshotTransfer()
    service = SimpleNamespace(storage=store, transfer=transfer)
    return HostMirrorService(service), store, transfer


async def capture(store, transfer, root):
    manifest = await transfer.capture(WorkspaceRef(locator=str(root)), store, "1")
    return store.put_manifest("1", manifest)


@pytest.mark.asyncio
async def test_process_writes_and_binary_rename_delete_are_mirrored(mirror, tmp_path):
    service, store, transfer = mirror
    remote, host = tmp_path / "sandbox", tmp_path / "host"
    remote.mkdir()
    (remote / "old.bin").write_bytes(bytes(range(256)))
    first = await capture(store, transfer, remote)
    state = await service._apply(store, "1", {"path": str(host)}, first, 1)
    process = await asyncio.create_subprocess_exec(sys.executable, "-c",
        "from pathlib import Path; import sys,time; p=Path(sys.argv[1]); "
        "(p/'old.bin').rename(p/'new.bin'); (p/'python.txt').write_text('written'); time.sleep(1)", str(remote))
    try:
        for _ in range(100):
            if (remote / "python.txt").exists():
                break
            await asyncio.sleep(.02)
        second = await capture(store, transfer, remote)
        state = await service._apply(store, "1", state, second, 1)
        assert process.returncode is None
        assert not (host / "old.bin").exists()
        assert (host / "new.bin").read_bytes() == bytes(range(256))
        assert (host / "python.txt").read_text() == "written"
        (remote / "new.bin").unlink()
        state = await service._apply(store, "1", state, await capture(store, transfer, remote), 1)
        assert state["status"] == "synced" and not (host / "new.bin").exists()
    finally:
        await process.wait()


@pytest.mark.asyncio
async def test_host_only_edits_block_the_entire_batch_and_survive_restart(mirror, tmp_path):
    service, store, transfer = mirror
    remote, host = tmp_path / "sandbox", tmp_path / "host"
    remote.mkdir()
    (remote / "file.txt").write_text("initial")
    state = await service._apply(store, "1", {"path": str(host)}, await capture(store, transfer, remote), 1)
    (host / "file.txt").write_text("host edit")
    (remote / "added.txt").write_text("remote edit")
    revision = await capture(store, transfer, remote)
    state = await service._apply(store, "1", state, revision, 1)
    assert state["status"] == "conflict" and state["conflicts"] == ["file.txt"]
    assert not (host / "added.txt").exists()
    restarted = HostMirrorService(SimpleNamespace(storage=store, transfer=transfer))
    again = await restarted._apply(store, "1", json.loads(json.dumps(state)), revision, 1)
    assert again["resolution_token"] == state["resolution_token"]
    current, _ = _capture(host)
    assert state["resolution_token"] == resolution_token(1, revision, state["revision"], current)
    (host / "file.txt").write_text("newer host edit")
    with pytest.raises(ProjectWorkspaceError, match="after conflict approval"):
        await restarted._apply(store, "1", state, revision, 1, force=True, expected_current=current)
    assert (host / "file.txt").read_text() == "newer host edit"


@pytest.mark.asyncio
async def test_failed_publish_retries_without_losing_the_baseline(mirror, tmp_path, monkeypatch):
    import agent.modules.projects.local_sync as local_sync
    service, store, transfer = mirror
    remote, host = tmp_path / "sandbox", tmp_path / "host"
    remote.mkdir()
    (remote / "file.txt").write_text("initial")
    state = await service._apply(store, "1", {"path": str(host)}, await capture(store, transfer, remote), 1)
    (remote / "file.txt").write_text("updated")
    revision = await capture(store, transfer, remote)
    with monkeypatch.context() as patch:
        patch.setattr(local_sync.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("file locked")))
        with pytest.raises(PermissionError):
            await service._apply(store, "1", state, revision, 1)
    assert (host / "file.txt").read_text() == "initial"
    result = await service._apply(store, "1", state, revision, 1)
    assert result["status"] == "synced" and (host / "file.txt").read_text() == "updated"


@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "expected"), [
    ("Sandbox not found (404)", "destroyed"),
    ("Provider connection timed out", "unknown"),
])
async def test_daytona_status_distinguishes_deleted_sandboxes_from_outages(monkeypatch, message, expected):
    def unavailable(ref):
        raise RuntimeError(message)
    monkeypatch.setattr("agent.modules.workspaces.daytona_backend.get_daytona_sandbox", unavailable)
    assert await workspace_runtime_status(WorkspaceRef(backend="daytona", locator="sandbox")) == expected


def test_monitor_fallback_catches_untracked_process_changes_and_restart(tmp_path, monkeypatch):
    import agent.modules.workspaces.filesystem_monitor as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monitor = FilesystemMonitor(str(tmp_path))
    monitor.close()
    initial = monitor.poll()
    subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('python')",
                    str(tmp_path / "script.txt")], check=True)
    clock[0] += 1.1
    changed = monitor.poll(initial["token"])
    assert changed["changed"] and changed["mode"] == "scan"
    assert not monitor.poll(changed["token"])["changed"]
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "cache").write_text("ignored")
    clock[0] += 1.1
    assert not monitor.poll(changed["token"])["changed"]
    restarted = FilesystemMonitor(str(tmp_path))
    try:
        assert restarted.poll(changed["token"])["changed"]
    finally:
        restarted.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux inotify")
def test_native_monitor_catches_new_nested_directories_and_recovers_lost_events(tmp_path, monkeypatch):
    import agent.modules.workspaces.filesystem_monitor as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monitor = FilesystemMonitor(str(tmp_path))
    try:
        initial = monitor.poll()
        assert initial["mode"] == "inotify"
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "file").write_text("new")
        monitor.poll(initial["token"])
        clock[0] += .31
        changed = monitor.poll(initial["token"])
        assert changed["changed"]
        (tmp_path / "nested" / "file").write_text("updated")
        monitor._events(clock[0])
        monitor.dirty_since = None
        clock[0] += 31
        assert monitor.poll(changed["token"])["changed"]
    finally:
        monitor.close()


def test_approved_overlay_rechecks_sandbox_before_any_write(tmp_path):
    root, stage = tmp_path / "sandbox", tmp_path / "stage"
    root.mkdir()
    stage.mkdir()
    (root / "file.txt").write_text("cloud")
    with tempfile_capture(root) as expected:
        (stage / "expected.json").write_text(json.dumps(expected))
        (stage / "manifest.json").write_text(json.dumps(expected))
    (root / "file.txt").write_text("newer cloud")
    with pytest.raises(ValueError, match="Sandbox files changed"):
        snapshot_worker.overlay(str(root), str(stage))
    assert (root / "file.txt").read_text() == "newer cloud"


def test_monitor_and_snapshot_import_from_the_shipped_bundle_without_application_dependencies(tmp_path):
    from agent.modules.tools.coding.remote import build_runtime_bundle
    bundle = tmp_path / "runtime.zip"
    bundle.write_bytes(build_runtime_bundle())
    root = tmp_path / "workspace"
    root.mkdir()
    code = ("import sys; sys.path.insert(0,sys.argv[1]); "
            "from agent.modules.workspaces.filesystem_monitor import FilesystemMonitor; "
            "monitor=FilesystemMonitor(sys.argv[2]); assert monitor.poll()['changed']; monitor.close(); "
            "assert 'sqlalchemy' not in sys.modules; assert 'pydantic' not in sys.modules")
    subprocess.run([sys.executable, "-I", "-c", code, str(bundle), str(root)], cwd=tmp_path, check=True, timeout=15)


def test_native_event_overflow_forces_a_scan_even_early_in_system_uptime(tmp_path):
    monitor = FilesystemMonitor(str(tmp_path))
    monitor.close()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, struct.pack("iIII", -1, 0x00004000, 0, 0))
    os.close(write_fd)
    monitor.fd = read_fd
    try:
        assert monitor._events(1.0)
        assert monitor.last_scan == float("-inf")
        assert monitor.dirty_since == 1.0
    finally:
        monitor.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows host compatibility")
def test_windows_host_resolution_preserves_cloud_executable_permissions():
    from agent.modules.workspaces.host_mirror import cloud_entry, validate_host_names
    assert cloud_entry({"type": "file", "mode": 0o666}, {"type": "file", "mode": 0o755})["mode"] == 0o755
    assert cloud_entry({"type": "file", "mode": 0o444}, {"type": "file", "mode": 0o755})["mode"] == 0o555
    assert cloud_entry({"type": "directory", "mode": 0o777}, {"type": "directory", "mode": 0o700})["mode"] == 0o700
    for entries in ({"CON.txt": {}}, {"trailing.": {}}, {"File": {}, "file": {}}):
        with pytest.raises(ProjectWorkspaceError) as error:
            validate_host_names({"entries": entries})
        assert error.value.code == "host_path_unsupported"


@contextmanager
def tempfile_capture(root):
    with tempfile.TemporaryDirectory(prefix="k41-test-") as stage:
        yield snapshot_worker.capture(str(root), stage)
