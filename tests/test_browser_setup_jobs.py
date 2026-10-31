"""Background browser setup uses fixed CLI commands and reports failures."""

import subprocess
import time
from threading import Event
from types import SimpleNamespace

import pytest

from agent.bootstrap import browser_setup


def finished(manager):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.status()
        if result and result["state"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("Browser setup did not finish")


def test_job_returns_immediately_rejects_duplicates_and_runs_custom_path_without_shell(monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(returncode=0, stdout="Browser is ready\n")

    monkeypatch.setattr(browser_setup.subprocess, "run", run)
    manager = browser_setup.BrowserSetupManager()
    path = "/chrome path/with spaces;echo injected"
    job = manager.start("check", path)
    try:
        assert job["state"] == "running"
        assert entered.wait(2)
        with pytest.raises(browser_setup.BrowserSetupBusyError):
            manager.start("repair")
        assert calls[0][0][-3:] == ["check", "--executable-path", path]
        assert calls[0][1].get("shell", False) is False
        assert calls[0][1]["stdin"] == subprocess.DEVNULL
    finally:
        release.set()
    result = finished(manager)
    assert result["id"] == job["id"]
    assert result["state"] == "succeeded"
    assert result["configured_executable_path"] == path
    assert "Browser is ready" in result["output"]


def test_repair_verifies_configured_override_and_does_not_hide_failed_check(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if "--force" in command:
            return SimpleNamespace(returncode=0, stdout="Installed system Chrome\n")
        return SimpleNamespace(returncode=1, stdout="Configured executable does not exist\n")

    monkeypatch.setattr(browser_setup.subprocess, "run", run)
    manager = browser_setup.BrowserSetupManager()
    manager.start("repair", "/wrong/chrome")
    result = finished(manager)
    assert commands[0][-2:] == ["install", "--force"]
    assert commands[1][-3:] == ["check", "--executable-path", "/wrong/chrome"]
    assert result["state"] == "failed"
    assert "Installed system Chrome" in result["output"]
    assert "does not exist" in result["output"]
    assert result["error"]


def test_install_failure_retains_bounded_output_and_allows_retry(monkeypatch):
    monkeypatch.setattr(browser_setup.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="x" * 40000 + "sudo: a password is required",
    ))
    manager = browser_setup.BrowserSetupManager()
    manager.start("install")
    result = finished(manager)
    assert result["state"] == "failed"
    assert len(result["output"]) == 32768
    assert result["output"].endswith("sudo: a password is required")
    manager.start("check")
    assert finished(manager)["id"] != result["id"]


@pytest.mark.parametrize("failure", [OSError("Cannot spawn Python"), subprocess.TimeoutExpired("check", 1800, output=b"partial output")])
def test_process_failure_never_reports_success(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(browser_setup.subprocess, "run", fail)
    manager = browser_setup.BrowserSetupManager()
    manager.start("check")
    result = finished(manager)
    assert result["state"] == "failed"
    assert result["error"]


def test_unknown_action_cannot_start_a_process():
    manager = browser_setup.BrowserSetupManager()
    with pytest.raises(ValueError):
        manager.start("shell")
    assert manager.status() is None
