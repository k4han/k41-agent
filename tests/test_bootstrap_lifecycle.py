"""CLI lifecycle guards against incomplete shutdown and duplicate starts."""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest
from typer.testing import CliRunner

from agent.bootstrap import cli, process_utils, service


runner = CliRunner()


@pytest.fixture
def server(tmp_path, monkeypatch):
    class Server:
        pid = 123
        alive = True
        same_process = True
        now = 0.0
        exit_after = None
        on_exit = None
        command = ["python", "-m", "agent.bootstrap.cli", "--no-tray"]

        def cmdline(self):
            return self.command

        def is_running(self):
            return self.alive and self.same_process

        def status(self):
            return psutil.STATUS_RUNNING if self.alive else psutil.STATUS_DEAD

        def sleep(self, seconds):
            self.now += seconds
            if self.exit_after is not None and self.now >= self.exit_after:
                self.alive = False
                if self.on_exit:
                    self.on_exit()

    instance = Server()
    monkeypatch.setattr(cli, "PID_FILE", tmp_path / "server.pid")
    monkeypatch.setattr(cli, "SHUTDOWN_SIGNAL", tmp_path / "shutdown.signal")
    monkeypatch.setattr(cli, "TRAY_PID_FILE", tmp_path / "tray.pid")
    monkeypatch.setattr(cli, "_server_health_reachable", lambda: False)
    monkeypatch.setattr(cli, "_is_tray_running", lambda: False)
    monkeypatch.setattr(cli, "_sync_autostart_from_config", lambda config: None)
    monkeypatch.setattr(service, "uses_systemd_service", lambda: False)
    monkeypatch.setattr(psutil, "Process", lambda pid: instance)
    monkeypatch.setattr(process_utils.time, "monotonic", lambda: instance.now)
    monkeypatch.setattr(process_utils.time, "sleep", instance.sleep)
    monkeypatch.delenv("K41_DAEMONIZED", raising=False)
    cli.PID_FILE.write_text(str(instance.pid))
    return instance


def test_stop_timeout_keeps_pid_and_returns_failure(server):
    result = runner.invoke(cli.app, ["stop", "--timeout", "1"])
    assert result.exit_code == 1, result.output
    assert "still alive" in result.output
    assert cli.PID_FILE.read_text() == "123"
    assert cli.SHUTDOWN_SIGNAL.read_text() == "123"
    assert server.alive


def test_stop_waits_beyond_old_five_second_limit(server):
    server.exit_after = 7
    result = runner.invoke(cli.app, ["stop"])
    assert result.exit_code == 0, result.output
    assert server.now >= 7
    assert not cli.PID_FILE.exists()
    assert not cli.SHUTDOWN_SIGNAL.exists()


def test_stop_retains_replacement_pid_and_shutdown_request(server):
    server.exit_after = 1

    def replace_files():
        cli.PID_FILE.write_text("456")
        cli.SHUTDOWN_SIGNAL.write_text("456")

    server.on_exit = replace_files
    result = runner.invoke(cli.app, ["stop"])
    assert result.exit_code == 0, result.output
    assert cli.PID_FILE.read_text() == "456"
    assert cli.SHUTDOWN_SIGNAL.read_text() == "456"


@pytest.mark.parametrize("value", ["invalid", "0", "-1"])
def test_stop_preserves_invalid_pid(server, value):
    cli.PID_FILE.write_text(value)
    result = runner.invoke(cli.app, ["stop"])
    assert result.exit_code == 1
    assert cli.PID_FILE.read_text() == value
    assert not cli.SHUTDOWN_SIGNAL.exists()


@pytest.mark.parametrize("command", [
    [], ["python", r"C:\k41-agent\unrelated.py"],
    ["python", "-m", "agent.bootstrap.cli", "update", "--yes"],
    ["python", "-m", "agent.bootstrap.cli", "restart"],
])
def test_stop_never_signals_an_unverified_process(server, command):
    server.command = command
    result = runner.invoke(cli.app, ["stop"])
    assert result.exit_code == 1
    assert cli.PID_FILE.exists()
    assert not cli.SHUTDOWN_SIGNAL.exists()


def test_stop_retains_pid_when_process_access_is_denied(server, monkeypatch):
    def denied():
        raise psutil.AccessDenied(server.pid)

    monkeypatch.setattr(server, "cmdline", denied)
    result = runner.invoke(cli.app, ["stop"])
    assert result.exit_code == 1
    assert "Cannot verify" in result.output
    assert cli.PID_FILE.exists()


def test_stop_does_not_report_success_with_missing_pid_and_live_health(server, monkeypatch):
    cli.PID_FILE.unlink()
    monkeypatch.setattr(cli, "_server_health_reachable", lambda: True)
    result = runner.invoke(cli.app, ["stop"])
    assert result.exit_code == 1
    assert "still reachable" in result.output


@pytest.mark.parametrize("action", ["stop", "restart"])
def test_systemd_failure_keeps_pid_without_fallback(server, monkeypatch, action):
    monkeypatch.setattr(service, "uses_systemd_service", lambda: True)

    def failed():
        raise RuntimeError("systemd stop failed")

    monkeypatch.setattr(service, "stop_service", failed)
    monkeypatch.setattr(cli, "serve", lambda **kwargs: pytest.fail("unexpected startup"))
    result = runner.invoke(cli.app, [action])
    assert result.exit_code == 1
    assert "systemd stop failed" in result.output
    assert cli.PID_FILE.exists()
    assert not cli.SHUTDOWN_SIGNAL.exists()


def test_systemd_stop_works_without_pid(server, monkeypatch):
    cli.PID_FILE.unlink()
    calls = []
    monkeypatch.setattr(service, "uses_systemd_service", lambda: True)
    monkeypatch.setattr(service, "stop_service", lambda: calls.append("stop"))
    result = runner.invoke(cli.app, ["stop"])
    assert result.exit_code == 0, result.output
    assert calls == ["stop"]


def test_systemd_stop_retains_pid_when_tracked_server_survives(server, monkeypatch):
    monkeypatch.setattr(service, "uses_systemd_service", lambda: True)
    monkeypatch.setattr(service, "stop_service", lambda: None)
    result = runner.invoke(cli.app, ["stop", "--timeout", "1"])
    assert result.exit_code == 1
    assert cli.PID_FILE.exists()


def test_restart_aborts_before_start_when_shutdown_times_out(server, monkeypatch):
    monkeypatch.setattr(cli, "serve", lambda **kwargs: pytest.fail("unexpected startup"))
    result = runner.invoke(cli.app, ["restart", "--timeout", "1"])
    assert result.exit_code == 1
    assert cli.PID_FILE.exists()


@pytest.mark.parametrize("running", [True, False])
def test_restart_starts_only_after_confirmed_shutdown(server, monkeypatch, running):
    calls = []
    if running:
        server.exit_after = 1
    else:
        cli.PID_FILE.unlink()

    def start(**kwargs):
        assert not cli.PID_FILE.exists()
        calls.append(kwargs)

    monkeypatch.setattr(cli, "serve", start)
    result = runner.invoke(cli.app, ["restart", "--no-tray"])
    assert result.exit_code == 0, result.output
    assert calls == [{"tray": False, "no_tray": True}]


def test_restart_with_tray_aborts_when_tray_cannot_stop(server, monkeypatch):
    server.exit_after = 1
    monkeypatch.setattr(cli, "_is_tray_running", lambda: True)
    monkeypatch.setattr(cli, "_stop_tray_process", lambda: False)
    monkeypatch.setattr(cli, "serve", lambda **kwargs: pytest.fail("unexpected startup"))
    result = runner.invoke(cli.app, ["restart", "--with-tray"])
    assert result.exit_code == 1
    assert "Could not stop tray" in result.output


def test_stop_with_tray_still_stops_tray_when_server_is_missing(server, monkeypatch):
    cli.PID_FILE.unlink()
    calls = []
    monkeypatch.setattr(cli, "_is_tray_running", lambda: True)
    monkeypatch.setattr(cli, "_stop_tray_process", lambda: calls.append("stop tray") or True)
    result = runner.invoke(cli.app, ["stop", "--with-tray"])
    assert result.exit_code == 1
    assert calls == ["stop tray"]


@pytest.fixture
def startup(server, monkeypatch):
    cli.PID_FILE.unlink()
    config = SimpleNamespace(host="127.0.0.1", port=4141, enable_web=True, tray_enabled=False)
    monkeypatch.setattr("agent.bootstrap.settings.load_bootstrap_config", lambda: config)
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: SimpleNamespace(status_code=503))
    return config


def test_restart_daemon_does_not_replay_restart_subcommand(startup, server, monkeypatch):
    commands = []
    monkeypatch.setattr(sys, "argv", ["k41", "restart", "--timeout", "12", "--no-tray"])
    monkeypatch.setattr(process_utils, "spawn_detached_process", lambda command, *args, **kwargs: commands.append(command))
    monkeypatch.setattr(cli, "_wait_for_server_startup", lambda *args, **kwargs: True)
    result = runner.invoke(cli.app, ["restart", "--no-tray"])
    assert result.exit_code == 0, result.output
    assert len(commands) == 1
    assert commands[0][1:] == ["-m", "agent.bootstrap.cli", "--no-tray"]


def test_startup_failure_returns_nonzero(startup, monkeypatch):
    monkeypatch.setattr(process_utils, "spawn_detached_process", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_wait_for_server_startup", lambda *args, **kwargs: False)
    result = runner.invoke(cli.app, ["--no-tray"])
    assert result.exit_code == 1
    assert "could not be confirmed" in result.output


def test_alive_pid_without_health_is_not_ready(startup, server, monkeypatch):
    cli.PID_FILE.write_text("123")
    monkeypatch.setattr(cli, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr(cli, "_is_k41_process", lambda pid: True)
    assert cli._wait_for_server_startup(startup, timeout_seconds=1) is False


def test_systemd_restart_stops_then_starts_without_detached_process(startup, server, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "uses_systemd_service", lambda: True)
    monkeypatch.setattr(service, "stop_service", lambda: calls.append("stop"))
    monkeypatch.setattr(service, "get_service_state", lambda: {"ActiveState": "inactive"})
    monkeypatch.setattr(service, "start_service", lambda: calls.append("start"))
    monkeypatch.setattr(process_utils, "spawn_detached_process", lambda *args, **kwargs: pytest.fail("unexpected detached process"))
    result = runner.invoke(cli.app, ["restart", "--no-tray"])
    assert result.exit_code == 0, result.output
    assert calls == ["stop", "start"]


def test_start_does_not_fall_back_when_systemd_is_unreachable(startup, monkeypatch):
    monkeypatch.setattr(service, "uses_systemd_service", lambda: True)

    def unreachable():
        raise RuntimeError("systemd user manager is not reachable")

    monkeypatch.setattr(service, "get_service_state", unreachable)
    monkeypatch.setattr(process_utils, "spawn_detached_process", lambda *args, **kwargs: pytest.fail("unexpected detached process"))
    result = runner.invoke(cli.app, ["--no-tray"])
    assert result.exit_code == 1
    assert "not reachable" in result.output


def test_foreground_keeps_pid_until_process_exit_is_verified(startup, monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.bootstrap.app", SimpleNamespace(run=lambda: None))
    result = runner.invoke(cli.app, ["--foreground", "--no-tray"])
    assert result.exit_code == 0, result.output
    assert cli.PID_FILE.read_text() == str(os.getpid())


@pytest.mark.parametrize("responds", [True, False])
def test_stop_tracks_real_process_until_exit(tmp_path, monkeypatch, responds):
    pid_file = tmp_path / "server.pid"
    signal_file = tmp_path / "shutdown.signal"
    entrypoint = tmp_path / "k41"
    entrypoint.write_text(
        "import os, time\n"
        "from pathlib import Path\n"
        f"pid_file = Path({str(pid_file)!r})\n"
        f"signal_file = Path({str(signal_file)!r})\n"
        "pid_file.write_text(str(os.getpid()))\n"
        "while True:\n"
        f"    if {responds!r} and signal_file.exists():\n"
        "        time.sleep(0.2)\n"
        "        break\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PID_FILE", pid_file)
    monkeypatch.setattr(cli, "SHUTDOWN_SIGNAL", signal_file)
    monkeypatch.setattr(cli, "_server_health_reachable", lambda: False)
    monkeypatch.setattr(service, "uses_systemd_service", lambda: False)
    process = subprocess.Popen([sys.executable, str(entrypoint), "--no-tray"])
    server_process = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pid_file.exists() and pid_file.read_text().strip().isdigit():
                break
            time.sleep(0.02)
        assert pid_file.exists()
        # Windows virtualenv launchers may run Python in a child process.
        server_pid = int(pid_file.read_text())
        server_process = psutil.Process(server_pid)
        result = runner.invoke(cli.app, ["stop", "--timeout", "1"])
        if responds:
            assert result.exit_code == 0, result.output
            assert process.wait(timeout=5) == 0
            assert not pid_file.exists()
        else:
            assert result.exit_code == 1, result.output
            assert process.poll() is None
            assert pid_file.read_text() == str(server_pid)
    finally:
        if server_process is not None and server_process.is_running():
            server_process.kill()
            server_process.wait(timeout=5)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
