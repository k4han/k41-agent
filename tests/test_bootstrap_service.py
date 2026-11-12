from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.bootstrap import service as service_module


def _paths(tmp_path: Path) -> service_module.ServicePaths:
    agent_home = tmp_path / "k41-agent"
    return service_module.ServicePaths(
        agent_home=agent_home,
        app_dir=agent_home / "app",
        python_exe=agent_home / "envs" / "bin" / "python",
        service_file=tmp_path / "k41-agent.service",
    )


def test_render_unit_uses_foreground_no_tray(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    content = service_module.render_unit(paths)
    assert "[Unit]" in content
    assert "WantedBy=default.target" in content
    assert "Restart=on-failure" in content
    assert "--foreground --no-tray" in content
    assert "TimeoutStopSec=30" in content
    assert str(paths.python_exe) in content
    assert str(paths.app_dir) in content
    assert "-m agent.bootstrap.cli" in content
    assert "-m agent.bootstrap.logs" in content
    assert str(service_module.SERVER_LOG_FILE) in content
    assert "StandardOutput=journal" in content
    assert "StandardError=journal" in content
    assert "append:" not in content
    assert "KillMode=mixed" in content


def test_resolve_service_paths_prefers_env_home(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("K41_AGENT_HOME", str(custom))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    paths = service_module.resolve_service_paths()
    assert paths.agent_home == custom.resolve()
    assert paths.app_dir == custom.resolve() / "app"


def test_install_service_writes_unit_and_enables(monkeypatch, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    calls: list[list[str]] = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (True, "ok"))
    monkeypatch.setattr(service_module, "resolve_service_paths", lambda **kwargs: paths)

    def fake_run(args: list[str], timeout: float = 30.0):
        calls.append(args)
        if args[0] == "show":
            return subprocess.CompletedProcess(args, 0, "ActiveState=active\nSubState=running\nMainPID=123\nControlPID=0\n", "")
        return FakeResult()

    monkeypatch.setattr(service_module, "_run_systemctl", fake_run)

    result = service_module.install_service(start=True)
    assert result.service_file.exists()
    content = result.service_file.read_text(encoding="utf-8")
    assert "k41-agent" in content.lower()
    flat = [" ".join(c) for c in calls]
    assert any("daemon-reload" in c for c in flat)
    assert any("enable" in c and "k41-agent.service" in c for c in flat)


def test_install_service_refuses_without_systemd(monkeypatch, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (False, "no systemd"))
    monkeypatch.setattr(service_module, "resolve_service_paths", lambda **kwargs: paths)
    try:
        service_module.install_service()
    except RuntimeError as exc:
        assert "no systemd" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")


def test_service_cli_status_reports_paths(monkeypatch) -> None:
    from typer.testing import CliRunner

    import agent.bootstrap.cli as cli_module

    fake_status = {
        "service": "k41-agent.service",
        "service_file": "/home/u/.config/systemd/user/k41-agent.service",
        "agent_home": "/home/u/.local/share/k41-agent",
        "python_exe": "/home/u/.local/share/k41-agent/envs/bin/python",
        "systemd_available": True,
        "systemd_reason": "ok",
        "installed": True,
        "enabled": True,
        "active": False,
    }
    monkeypatch.setattr("agent.bootstrap.service.get_status", lambda: fake_status)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["service", "status"])
    assert result.exit_code == 0, result.output
    assert "k41-agent.service" in result.output


def test_service_cli_rejects_unknown_action() -> None:
    from typer.testing import CliRunner

    import agent.bootstrap.cli as cli_module

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["service", "bogus"])
    assert result.exit_code == 2
    assert "Unknown service action" in result.output


def test_stop_service_waits_for_slow_shutdown(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (True, "ok"))

    def slow_stop(command, **kwargs):
        if "show" in command:
            return subprocess.CompletedProcess(command, 0, "ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n", "")
        assert command == ["systemctl", "--user", "stop", service_module.SERVICE_NAME]
        # Older installed units may take systemd's default 90 seconds to stop.
        if kwargs["timeout"] <= 90:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(service_module.subprocess, "run", slow_stop)
    service_module.stop_service()


def test_stop_service_preserves_systemd_error(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (True, "ok"))
    monkeypatch.setattr(
        service_module.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "Access denied"),
    )
    with pytest.raises(RuntimeError, match="Access denied"):
        service_module.stop_service()


@pytest.mark.parametrize("active, main_pid, control_pid", [
    ("active", "123", "0"), ("deactivating", "0", "456"),
    ("inactive", "123", "0"), ("failed", "0", "456"),
])
def test_stop_service_rejects_incomplete_shutdown(monkeypatch, active, main_pid, control_pid):
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (True, "ok"))
    state = f"ActiveState={active}\nSubState=stop\nMainPID={main_pid}\nControlPID={control_pid}\n"
    monkeypatch.setattr(service_module, "_run_systemctl", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, state, ""))
    with pytest.raises(RuntimeError, match="has not stopped"):
        service_module.stop_service()


@pytest.mark.parametrize("state", ["", "ActiveState=inactive\n", "ActiveState=active\nMainPID=garbage\n"])
def test_service_state_must_be_verifiable(monkeypatch, state):
    monkeypatch.setattr(service_module, "_run_systemctl", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, state, ""))
    with pytest.raises(RuntimeError, match="Could not verify"):
        service_module.get_service_state()


def test_restart_service_allows_slow_stop_and_verifies_start(monkeypatch):
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (True, "ok"))
    calls = []

    def run(args, timeout=30.0):
        calls.append(args[0])
        if args[0] == "restart":
            assert timeout > 90
        return subprocess.CompletedProcess(args, 0, "ActiveState=active\nSubState=running\nMainPID=123\nControlPID=0\n", "")

    monkeypatch.setattr(service_module, "_run_systemctl", run)
    service_module.restart_service()
    assert calls == ["restart", "show"]


@pytest.mark.parametrize("action", [service_module.start_service, service_module.restart_service])
def test_service_start_does_not_report_success_for_failed_unit(monkeypatch, action):
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (True, "ok"))
    monkeypatch.setattr(service_module, "_run_systemctl", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "ActiveState=failed\nSubState=failed\nMainPID=0\nControlPID=0\n", ""))
    with pytest.raises(RuntimeError, match="has not started"):
        action()


def test_uninstall_keeps_unit_when_stop_fails(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    paths.service_file.write_text("unit")
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (True, "ok"))
    monkeypatch.setattr(service_module, "_run_systemctl", lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "Stop failed"))
    with pytest.raises(RuntimeError, match="Stop failed"):
        service_module.uninstall_service(paths=paths)
    assert paths.service_file.exists()


def test_uninstall_keeps_unit_when_manager_is_unreachable(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    paths.service_file.write_text("unit")
    monkeypatch.setattr(service_module, "is_systemd_available", lambda: (False, "No user bus"))
    with pytest.raises(RuntimeError, match="Unit file retained"):
        service_module.uninstall_service(paths=paths)
    assert paths.service_file.exists()


@pytest.mark.parametrize("platform, installed, expected", [
    ("linux", True, True), ("linux", False, False),
    ("win32", True, False), ("darwin", True, False),
])
def test_only_installed_linux_services_use_systemd(monkeypatch, platform, installed, expected):
    monkeypatch.setattr(service_module.sys, "platform", platform)
    monkeypatch.setattr(service_module, "is_service_installed", lambda: installed)
    assert service_module.uses_systemd_service() is expected
