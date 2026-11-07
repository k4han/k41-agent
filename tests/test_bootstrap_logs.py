from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from agent.bootstrap import logs, process_utils
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs


def test_log_rotation_preserves_output_and_appends_after_restart(tmp_path):
    path = tmp_path / "logs" / "server.log"
    with closing(logs.RotatingLogWriter(path, max_bytes=8, backup_count=5)) as writer:
        writer.write(b"first\n")
    with closing(logs.RotatingLogWriter(path, max_bytes=8, backup_count=5)) as writer:
        writer.write(b"second\nthird\n")
    assert Path(f"{path}.2").read_bytes() == b"first\n"
    assert Path(f"{path}.1").read_bytes() == b"second\n"
    assert path.read_bytes() == b"third\n"


def test_log_rotation_bounds_large_writes_and_removes_old_backups(tmp_path):
    path = tmp_path / "server.log"
    with closing(logs.RotatingLogWriter(path, max_bytes=8, backup_count=2)) as writer:
        writer.write(b"a" * 8 + b"b" * 8 + b"c" * 8 + b"tail")
    assert path.read_bytes() == b"tail"
    assert Path(f"{path}.1").read_bytes() == b"c" * 8
    assert Path(f"{path}.2").read_bytes() == b"b" * 8
    assert len(list(tmp_path.iterdir())) == 3
    assert all(item.stat().st_size <= 8 for item in tmp_path.iterdir())


@pytest.mark.parametrize("has_active_log", [False, True])
def test_log_rotation_recovers_staging_after_restart(tmp_path, has_active_log):
    path = tmp_path / "server.log"
    rotating = Path(f"{path}.rotating")
    rotating.write_bytes(b"staged\n")
    Path(f"{path}.1").write_bytes(b"backup\n")
    if has_active_log:
        path.write_bytes(b"active\n")

    with closing(logs.RotatingLogWriter(path, max_bytes=8, backup_count=3)) as writer:
        assert not rotating.exists()
        writer.write(b"new log\n")

    assert path.read_bytes() == b"new log\n"
    if has_active_log:
        assert Path(f"{path}.1").read_bytes() == b"active\n"
        assert Path(f"{path}.2").read_bytes() == b"staged\n"
        assert Path(f"{path}.3").read_bytes() == b"backup\n"
    else:
        assert Path(f"{path}.1").read_bytes() == b"staged\n"
        assert Path(f"{path}.2").read_bytes() == b"backup\n"


def locked_replace(monkeypatch, is_locked):
    original = Path.replace

    def replace(self, target):
        if is_locked(self):
            raise PermissionError(32, "in use", str(self))
        return original(self, target)

    monkeypatch.setattr(Path, "replace", replace)


def test_log_rotation_does_not_overwrite_staging_when_recovery_is_locked(tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    rotating = Path(f"{path}.rotating")
    with closing(logs.RotatingLogWriter(path, max_bytes=8, backup_count=3)) as writer:
        writer.write(b"active\n")
        rotating.write_bytes(b"staged\n")
        locked_replace(monkeypatch, lambda source: source == rotating)
        writer.write(b"lost\n")
        assert writer.dropped == 5
    assert path.read_bytes() == b"active\n"
    assert rotating.read_bytes() == b"staged\n"


def test_locked_log_keeps_backups_and_recovers_with_a_notice(tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    Path(f"{path}.1").write_bytes(b"newer\n")
    Path(f"{path}.2").write_bytes(b"older\n")
    locked = [True]
    locked_replace(monkeypatch, lambda source: locked[0] and source == path)
    with closing(logs.RotatingLogWriter(path, max_bytes=8, backup_count=3)) as writer:
        writer.write(b"current\n")
        for _ in range(4):
            writer.write(b"lost\n")
        assert path.read_bytes() == b"current\n"
        assert Path(f"{path}.1").read_bytes() == b"newer\n"
        assert Path(f"{path}.2").read_bytes() == b"older\n"
        assert not Path(f"{path}.3").exists() and not Path(f"{path}.rotating").exists()

        locked[0] = False
        writer.max_bytes = 1024
        writer.write(b"back\n")
    assert path.read_bytes() == b"current\n[20 bytes of output dropped: log file unavailable]\nback\n"


def test_failed_backup_shift_restores_the_active_log(tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    Path(f"{path}.1").write_bytes(b"backup\n")
    locked_replace(monkeypatch, lambda source: source == Path(f"{path}.1"))
    with closing(logs.RotatingLogWriter(path, max_bytes=8, backup_count=3)) as writer:
        writer.write(b"current\n")
        writer.write(b"lost\n")
    assert path.read_bytes() == b"current\n"
    assert Path(f"{path}.1").read_bytes() == b"backup\n"
    assert not Path(f"{path}.rotating").exists()


def test_log_writer_survives_a_failing_disk(tmp_path):
    path = tmp_path / "server.log"
    writer = logs.RotatingLogWriter(path, max_bytes=1024)

    class FullDisk:
        closed = False

        def write(self, data):
            raise OSError(28, "No space left on device")

        def flush(self):
            raise OSError(28, "No space left on device")

        def close(self):
            self.closed = True
            raise OSError(28, "No space left on device")

    writer.stream.close()
    writer.stream = FullDisk()
    writer.write(b"lost\n")
    assert writer.dropped == 5
    writer.close()


def test_log_failure_does_not_stop_the_logged_process(tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    locked_replace(monkeypatch, lambda source: source == path)
    code = (
        "import os, sys; "
        f"os.write(1, b'x' * {logs.LOG_MAX_BYTES}); "
        "os.write(1, b'more\\n'); "
        "sys.exit(7)"
    )
    assert logs.run_logged_process([sys.executable, "-c", code], path) == 7
    assert 0 < path.stat().st_size <= logs.LOG_MAX_BYTES


def test_log_wrapper_captures_native_output_tracebacks_and_exit_code(tmp_path):
    path = tmp_path / "logs" / "server.log"
    code = (
        "import os; "
        "os.write(1, b'stdout\\n'); "
        "os.write(2, b'stderr\\n'); "
        "raise RuntimeError('startup failed')"
    )
    result = subprocess.run(
        logs.logged_command([sys.executable, "-c", code], path, tee=True),
        capture_output=True,
        timeout=15,
        **hidden_subprocess_kwargs(),
    )
    output = path.read_bytes()
    assert result.returncode == 1
    assert output.startswith(b"stdout\nstderr\n")
    assert b"Traceback" in output
    assert b"RuntimeError: startup failed" in output
    assert result.stdout == output
    assert result.stderr == b""


def test_log_wrapper_rotates_during_a_running_process(tmp_path):
    path = tmp_path / "server.log"
    code = (
        "import os; "
        f"os.write(1, b'x' * {logs.LOG_MAX_BYTES}); "
        "os.write(2, b'final output\\n')"
    )
    result = subprocess.run(
        logs.logged_command([sys.executable, "-c", code], path),
        capture_output=True,
        timeout=15,
        **hidden_subprocess_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == b"final output\n"
    assert Path(f"{path}.1").read_bytes() == b"x" * logs.LOG_MAX_BYTES


def test_log_wrapper_records_launch_errors(tmp_path):
    path = tmp_path / "server.log"
    assert logs.run_logged_process([str(tmp_path / "missing-executable")], path) == 1
    assert b"FileNotFoundError" in path.read_bytes()


def test_detached_log_wrapper_preserves_environment_and_working_directory(tmp_path, monkeypatch):
    spawned = []
    original_popen = subprocess.Popen

    def capture_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(process_utils.subprocess, "Popen", capture_popen)
    path = tmp_path / "logs" / "server.log"
    code = "import os; print(os.environ['K41_LOG_TEST']); print(os.getcwd())"
    env = {**os.environ, "K41_LOG_TEST": "custom environment"}
    process_utils.spawn_detached_process([sys.executable, "-c", code], path, env=env, cwd=tmp_path)
    assert spawned[0].wait(timeout=15) == 0
    assert path.read_text(encoding="utf-8").splitlines() == ["custom environment", str(tmp_path)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX service signals")
def test_log_wrapper_forwards_shutdown_signal_and_drains_output(tmp_path):
    import time

    path = tmp_path / "server.log"
    code = (
        "import signal, sys, time\n"
        "def stop(signum, frame):\n"
        "    print('shutdown complete', flush=True)\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(0.1)\n"
    )
    process = subprocess.Popen(logs.logged_command([sys.executable, "-c", code], path))
    try:
        deadline = time.monotonic() + 10
        while not path.exists() or b"ready" not in path.read_bytes():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
        assert path.read_text() == "ready\nshutdown complete\n"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
