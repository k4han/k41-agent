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
