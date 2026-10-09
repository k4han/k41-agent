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
