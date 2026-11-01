"""Browser installation, failure handling, and isolated automation checks."""

import subprocess
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from agent.bootstrap import browser
from agent.bootstrap.cli import app
from agent.modules.browser import StagehandDriver


def linux_host(monkeypatch, uid=0):
    monkeypatch.setattr(browser, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(browser.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(browser.platform, "freedesktop_os_release", lambda: {"ID": "ubuntu"})
    monkeypatch.setattr(browser.os, "geteuid", lambda: uid, raising=False)
    monkeypatch.setattr(browser, "find_browser", lambda: None)
    monkeypatch.setattr(browser.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.mark.parametrize("uid,prefix", [(0, []), (1000, ["sudo", "-n"])])
def test_install_completes_pending_packages_before_downloading_chrome(monkeypatch, uid, prefix):
    linux_host(monkeypatch, uid)
    commands = []
    archives = []
    pending_configuration = True

    def stream(method, url, **kwargs):
        assert not pending_configuration
        assert method == "GET"
        assert url == browser.CHROME_DEB_URL
        return closing(httpx.Response(200, content=b"chrome-package", request=httpx.Request(method, url)))

    def run(command, **kwargs):
        nonlocal pending_configuration
        assert kwargs == {"check": True, "timeout": 600}
        commands.append(command)
        if "dpkg" in command:
            pending_configuration = False
        if "install" in command:
            if pending_configuration:
                raise subprocess.CalledProcessError(100, command, output="dpkg was interrupted")
            archive = Path(command[-1])
            assert archive.is_absolute()
            assert archive.read_bytes() == b"chrome-package"
            archives.append(archive)

    monkeypatch.setattr(httpx, "stream", stream)
    monkeypatch.setattr(browser.subprocess, "run", run)

    assert browser.install_browser() == "/usr/bin/google-chrome-stable"
    package_prefix = [*prefix, "env", "DEBIAN_FRONTEND=noninteractive"]
    assert commands[0] == [*package_prefix, "apt-get", "update"]
    assert commands[1] == [
        *package_prefix, "dpkg", "--force-confdef", "--force-confold", "--configure", "-a",
    ]
    assert commands[2][:-1] == [*package_prefix, "apt-get", "install", "-y", "--reinstall"]
    assert len(commands) == 3
    assert not archives[0].parent.exists()


def test_existing_browser_does_not_download_or_run_package_manager(monkeypatch):
    monkeypatch.setattr(browser, "find_browser", lambda: "/existing/chrome")

    def unexpected(*args, **kwargs):
        pytest.fail("An existing browser should be reused")

    monkeypatch.setattr(httpx, "stream", unexpected)
    monkeypatch.setattr(browser.subprocess, "run", unexpected)
    assert browser.install_browser() == "/existing/chrome"


def test_force_repairs_even_when_browser_exists(monkeypatch):
    linux_host(monkeypatch)
    monkeypatch.setattr(browser, "find_browser", lambda: "/existing/chrome")
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: closing(httpx.Response(
        200, content=b"package", request=httpx.Request("GET", browser.CHROME_DEB_URL),
    )))
    commands = []
    monkeypatch.setattr(browser.subprocess, "run", lambda command, **kwargs: commands.append(command))
    browser.install_browser(force=True)
    assert "install" in commands[-1]


@pytest.mark.parametrize("machine,distribution", [("aarch64", "ubuntu"), ("x86_64", "fedora")])
def test_unsupported_host_fails_before_download_or_package_changes(monkeypatch, machine, distribution):
    linux_host(monkeypatch)
    monkeypatch.setattr(browser.platform, "machine", lambda: machine)
    monkeypatch.setattr(browser.platform, "freedesktop_os_release", lambda: {"ID": distribution})

    def unexpected(*args, **kwargs):
        pytest.fail("Unsupported hosts must not install packages")

    monkeypatch.setattr(httpx, "stream", unexpected)
    monkeypatch.setattr(browser.subprocess, "run", unexpected)
    with pytest.raises(browser.BrowserSetupError, match="Debian/Ubuntu"):
        browser.install_browser()


def test_missing_sudo_fails_before_download(monkeypatch):
    linux_host(monkeypatch, uid=1000)
    monkeypatch.setattr(browser.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    with pytest.raises(browser.BrowserSetupError, match="requires root or sudo"):
        browser.install_browser()


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(1, ["sudo", "-n", "apt-get", "update"]),
    subprocess.TimeoutExpired(["apt-get", "update"], 600),
])
def test_package_failure_stops_install_before_download(monkeypatch, failure):
    linux_host(monkeypatch)

    def fail(*args, **kwargs):
        raise failure

    def unexpected(*args, **kwargs):
        pytest.fail("No download should occur after a package-manager failure")

    monkeypatch.setattr(browser.subprocess, "run", fail)
    monkeypatch.setattr(httpx, "stream", unexpected)
    with pytest.raises(browser.BrowserSetupError):
        browser.install_browser()


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(1, ["dpkg", "--configure", "-a"]),
    subprocess.TimeoutExpired(["dpkg", "--configure", "-a"], 600),
])
def test_pending_package_configuration_failure_stops_before_download(monkeypatch, failure):
    linux_host(monkeypatch)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if "dpkg" in command:
            raise failure

    def unexpected(*args, **kwargs):
        pytest.fail("No download should occur while pending packages cannot be configured")

    monkeypatch.setattr(browser.subprocess, "run", run)
    monkeypatch.setattr(httpx, "stream", unexpected)
    with pytest.raises(browser.BrowserSetupError, match="sudo dpkg --configure -a"):
        browser.install_browser()
    assert len(commands) == 2
    assert not any("install" in command for command in commands)


def test_install_command_reports_pending_package_failure_without_claiming_success(monkeypatch):
    linux_host(monkeypatch)

    def run(command, **kwargs):
        if "dpkg" in command:
            raise subprocess.CalledProcessError(1, command)
        assert "install" not in command

    def unexpected(*args, **kwargs):
        pytest.fail("Failed package configuration must stop installation and verification")

    monkeypatch.setattr(browser.subprocess, "run", run)
    monkeypatch.setattr(httpx, "stream", unexpected)
    monkeypatch.setattr(browser, "check_browser", unexpected)
    result = CliRunner().invoke(app, ["browser", "install"])
    assert result.exit_code == 1
    assert "sudo dpkg --configure -a" in result.output
    assert "Browser is ready" not in result.output


def test_download_failure_does_not_install_partial_package(monkeypatch):
    linux_host(monkeypatch)
    commands = []
    monkeypatch.setattr(browser.subprocess, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: closing(httpx.Response(
        404, request=httpx.Request("GET", browser.CHROME_DEB_URL),
    )))
    with pytest.raises(browser.BrowserSetupError, match="Could not download"):
        browser.install_browser()
    assert len(commands) == 2
    assert commands[0][-2:] == ["apt-get", "update"]
    assert commands[1][-3:] == ["--force-confold", "--configure", "-a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_check_uses_disposable_profile_and_closes_browser(monkeypatch, fails):
    captured = {}

    async def run(**kwargs):
        if fails:
            raise RuntimeError("Automation failed")
        return ""

    async def close():
        captured["closed"] = True

    async def launch(profile, artifacts, executable_path):
        assert artifacts.is_dir()
        captured.update(profile=profile, executable_path=executable_path)
        return SimpleNamespace(facade=SimpleNamespace(run=run), close=close)

    monkeypatch.setattr(StagehandDriver, "launch", launch)
    if fails:
        with pytest.raises(RuntimeError, match="Automation failed"):
            await browser.check_browser("/custom/chrome")
    else:
        await browser.check_browser("/custom/chrome")
    assert captured["executable_path"] == "/custom/chrome"
    assert captured["closed"] is True
    assert not captured["profile"].parent.exists()


def test_install_command_verifies_the_installed_executable(monkeypatch):
    captured = {}

    def install_browser(*, force):
        captured["force"] = force
        return "/installed/chrome"

    async def check_browser(path):
        captured["path"] = path

    monkeypatch.setattr(browser, "install_browser", install_browser)
    monkeypatch.setattr(browser, "check_browser", check_browser)
    result = CliRunner().invoke(app, ["browser", "install", "--force"])
    assert result.exit_code == 0, result.output
    assert captured == {"force": True, "path": "/installed/chrome"}
    assert "Browser is ready" in result.output


def test_failed_check_returns_nonzero_with_repair_hint(monkeypatch):
    async def fail(path):
        raise RuntimeError("Chrome exited before becoming ready")

    monkeypatch.setattr(browser, "check_browser", fail)
    result = CliRunner().invoke(app, ["browser", "check", "--executable-path", "/custom/chrome"])
    assert result.exit_code == 1
    assert "Chrome exited before becoming ready" in result.output
    assert "k41 browser install --force" in result.output
    assert "Browser is ready" not in result.output
