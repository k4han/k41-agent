from __future__ import annotations

import subprocess
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.bootstrap import update_state


def test_update_lock_serializes_processes_and_releases_after_error(tmp_path: Path) -> None:
    script = """
from pathlib import Path
import sys
from agent.bootstrap.update_state import UpdateBusyError, update_lock
try:
    with update_lock(Path(sys.argv[1])):
        print("acquired")
except UpdateBusyError:
    print("busy")
"""

    def probe() -> str:
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return result.stdout.strip()

    with pytest.raises(RuntimeError, match="interrupted"):
        with update_state.update_lock(tmp_path):
            assert probe() == "busy"
            raise RuntimeError("interrupted")
    assert probe() == "acquired"


def test_status_survives_restart_and_detects_dead_worker(tmp_path: Path, monkeypatch) -> None:
    status = {"update_id": "job", "status": "running", "pid": 123, "process_started_at": 100.0, "error": None}
    update_state.write_update_status(tmp_path, status)
    monkeypatch.setattr(update_state, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(update_state, "get_process_start_time", lambda pid: 100.0)
    assert update_state.read_update_status(tmp_path) == status
    monkeypatch.setattr(update_state, "is_process_alive", lambda pid: False)
    failed = update_state.read_update_status(tmp_path)
    assert failed["status"] == "failed"
    assert "exited unexpectedly" in failed["error"]
    update_state.write_update_status(tmp_path, {**status, "status": "updated", "latest_version": "0.1.3"})
    assert update_state.read_update_status(tmp_path)["status"] == "updated"


def test_queued_status_expires_if_child_never_starts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(update_state.time, "time", lambda: 100.0)
    update_state.write_update_status(tmp_path, {"update_id": "job", "status": "queued", "queued_at": 90.0})
    assert update_state.read_update_status(tmp_path)["status"] == "queued"
    monkeypatch.setattr(update_state.time, "time", lambda: 151.0)
    failed = update_state.read_update_status(tmp_path)
    assert failed["status"] == "failed"
    assert "did not start" in failed["error"]


@pytest.mark.parametrize("payload", ["invalid json", "[]", "null"])
def test_invalid_status_does_not_crash_reader(tmp_path: Path, payload: str) -> None:
    (tmp_path / "update-status.json").write_text(payload, encoding="utf-8")
    assert update_state.read_update_status(tmp_path) is None


def test_update_status_can_be_written_while_dashboard_reads_it(tmp_path: Path) -> None:
    update_state.write_update_status(tmp_path, {"update_id": "job", "status": "updated", "index": 0})

    def reader():
        for _ in range(2000):
            status = update_state.read_update_status(tmp_path)
            if status is not None:
                assert status["update_id"] == "job"

    with ThreadPoolExecutor(max_workers=5) as pool:
        readers = [pool.submit(reader) for _ in range(4)]
        for index in range(1000):
            update_state.write_update_status(tmp_path, {"update_id": "job", "status": "updated", "index": index})
        for future in readers:
            future.result()
    assert update_state.read_update_status(tmp_path)["index"] == 999


def test_startup_cleans_stale_status_files_and_preserves_recent_files(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from agent.bootstrap import runtime, update

    stale = tmp_path / f"update-status-{'a' * 32}.tmp"
    recent = tmp_path / f"update-status-{'b' * 32}.tmp"
    unrelated = tmp_path / "update-status-not-an-updater-file.tmp"
    for candidate in (stale, recent, unrelated):
        candidate.write_text("temporary content")
    past = update_state.time.time() - 120
    os.utime(stale, (past, past))
    os.utime(unrelated, (past, past))
    monkeypatch.setattr(update, "resolve_managed_install", lambda: SimpleNamespace(agent_home=tmp_path))
    runtime._cleanup_update_state()
    assert not stale.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_startup_cleanup_skips_an_active_update_writer(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from agent.bootstrap import runtime, update

    monkeypatch.setattr(update, "resolve_managed_install", lambda: SimpleNamespace(agent_home=tmp_path))
    with update_state.update_lock(tmp_path):
        active = tmp_path / f"update-status-{'a' * 32}.tmp"
        active.write_text("temporary content")
        past = update_state.time.time() - 120
        os.utime(active, (past, past))
        runtime._cleanup_update_state()
        assert active.exists()
    runtime._cleanup_update_state()
    assert not active.exists()


def test_running_status_detects_reused_pid(tmp_path: Path, monkeypatch) -> None:
    status = {"update_id": "job", "status": "running", "pid": 123, "process_started_at": 100.0}
    update_state.write_update_status(tmp_path, status)
    monkeypatch.setattr(update_state, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(update_state, "get_process_start_time", lambda pid: 100.0)
    assert update_state.read_update_status(tmp_path)["status"] == "running"
    monkeypatch.setattr(update_state, "get_process_start_time", lambda pid: 200.0)
    assert update_state.read_update_status(tmp_path)["status"] == "failed"


def test_legacy_running_status_checks_updater_command(tmp_path: Path, monkeypatch) -> None:
    update_state.write_update_status(tmp_path, {"update_id": "job", "status": "running", "pid": 123})
    monkeypatch.setattr(update_state, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(update_state, "get_process_cmdline", lambda pid: "python -m agent.bootstrap.cli update --yes")
    assert update_state.read_update_status(tmp_path)["status"] == "running"
    monkeypatch.setattr(update_state, "get_process_cmdline", lambda pid: "python -m agent.bootstrap.cli --no-tray")
    assert update_state.read_update_status(tmp_path)["status"] == "failed"


def test_process_creation_time_is_stable_for_current_process() -> None:
    first = update_state.get_process_start_time(os.getpid())
    assert isinstance(first, float)
    assert update_state.get_process_start_time(os.getpid()) == first
