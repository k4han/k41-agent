"""Install and verify the host browser used by browser tools."""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import typer

from agent.shared.infrastructure.browser import find_browser, managed_browser_path


app = typer.Typer(
    help="Install and check the local browser used by browser tools.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

CHROME_DEB_URL = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"


class BrowserSetupError(RuntimeError):
    """A browser setup failure with an actionable operator message."""


def _run_package_command(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise BrowserSetupError(
            f"Browser installation command failed (exit {exc.returncode}). "
            "Check the command output above. If sudo requires a password, "
            "run 'sudo -v', then retry as the same user."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BrowserSetupError("Browser installation timed out. Check network access and apt/dpkg locks, then retry.") from exc


def installation_unavailable_reason() -> str | None:
    """Describe unsupported hosts without downloading or changing packages."""
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        return (
            "Automatic installation supports Debian/Ubuntu x86_64 and ARM64. "
            "Install Chrome or Chromium for this platform, then run 'k41 browser check'."
        )
    release = platform.freedesktop_os_release()
    distributions = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    if not distributions.intersection({"debian", "ubuntu"}) or not shutil.which("apt-get"):
        return (
            "Automatic installation requires Debian/Ubuntu with apt-get. "
            "Install Chrome or Chromium with your system package manager."
        )
    return None


def install_browser(*, force: bool = False) -> str:
    """Reuse a browser or install Chrome/Chromium and its system dependencies."""
    if not force and (existing := find_browser()):
        typer.echo(f"Using existing browser: {existing}")
        return existing
    if reason := installation_unavailable_reason():
        raise BrowserSetupError(reason)
    prefix = []
    if os.geteuid() != 0:
        if not shutil.which("sudo"):
            raise BrowserSetupError("Installing a browser requires root or sudo. Ask an administrator to install Chrome or Chromium.")
        prefix = ["sudo", "-n"]
    # Set this after sudo so its environment filtering cannot restore prompts.
    prefix.extend(["env", "DEBIAN_FRONTEND=noninteractive"])
    # Check package-manager access before downloading a large browser package.
    typer.echo("Updating package metadata (requires root or cached sudo credentials)...")
    _run_package_command([*prefix, "apt-get", "update"])
    typer.echo("Completing pending system package configuration...")
    try:
        # Interrupted dpkg transactions block apt installs. Keep existing config
        # files when there is no default answer, including in dashboard jobs.
        _run_package_command([
            *prefix, "dpkg", "--force-confdef", "--force-confold", "--configure", "-a",
        ])
    except BrowserSetupError as exc:
        raise BrowserSetupError(
            f"Could not complete pending system package configuration. {exc} "
            "Run 'sudo dpkg --configure -a' in a terminal on the agent host, "
            "resolve the reported package errors, then retry browser installation."
        ) from exc

    if platform.machine().lower() in {"aarch64", "arm64"}:
        return _install_arm_chromium(prefix, force=force)

    import httpx

    with TemporaryDirectory(prefix="k41-browser-install-") as directory:
        archive = Path(directory) / "google-chrome-stable.deb"
        typer.echo("Downloading Google Chrome from dl.google.com...")
        try:
            with httpx.stream("GET", CHROME_DEB_URL, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                with archive.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
        except httpx.HTTPError as exc:
            raise BrowserSetupError(f"Could not download Chrome: {exc}") from exc
        # apt's unprivileged download helper must be able to read the local package.
        Path(directory).chmod(0o755)
        archive.chmod(0o644)
        typer.echo("Installing Chrome and required system libraries...")
        _run_package_command([*prefix, "apt-get", "install", "-y", "--reinstall", str(archive)])
    executable = shutil.which("google-chrome-stable") or shutil.which("google-chrome")
    if not executable:
        raise BrowserSetupError("Chrome installation finished, but its executable was not found on PATH.")
    return executable


def _install_arm_chromium(prefix: list[str], *, force: bool) -> str:
    """Install full Chromium without Snap's profile and extension restrictions."""
    from playwright.sync_api import sync_playwright

    command = [sys.executable, "-m", "playwright"]
    typer.echo("Installing Chromium system libraries...")
    _run_package_command([*prefix, *command, "install-deps", "chromium"])
    typer.echo("Downloading Chromium for ARM64...")
    # Download as the agent user; only system package installation needs sudo.
    # The full browser is required for Stagehand's extension in headless mode.
    _run_package_command([*command, "install", "--no-shell", *(["--force"] if force else []), "chromium"])
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise BrowserSetupError("Chromium installation finished, but its executable was not found or is not executable.")
    link = managed_browser_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(executable)
    return str(link)


async def check_browser(executable_path: str = "") -> None:
    """Check the real automation stack with an isolated, disposable profile."""
    from agent.modules.browser import StagehandDriver

    with TemporaryDirectory(prefix="k41-browser-check-") as directory:
        root = Path(directory)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        driver = await asyncio.wait_for(
            StagehandDriver.launch(root / "profile", artifacts, executable_path), 60,
        )
        try:
            await asyncio.wait_for(driver.facade.run(code="return await page.title();"), 15)
        finally:
            await asyncio.wait_for(driver.close(), 15)


def _verify(executable_path: str) -> None:
    typer.echo("Checking headless browser startup and automation...")
    try:
        asyncio.run(check_browser(executable_path))
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        raise BrowserSetupError(
            f"Browser check failed: {detail}. Run 'k41 browser install --force' to repair "
            "Chrome/Chromium and its system dependencies on Debian/Ubuntu x86_64 or ARM64. "
            "On other platforms, install a browser and set browser.executable_path or CHROME_PATH. "
            "If the browser still exits, check AppArmor/container restrictions and available memory."
        ) from exc
    typer.echo("Browser is ready for browser tools. No desktop session is required.")


@app.command("install")
def install(
    force: bool = typer.Option(False, "--force", help="Reinstall Chrome/Chromium and repair system dependencies."),
) -> None:
    """Install Chrome/Chromium on Debian/Ubuntu x86_64 or ARM64 and verify automation."""
    try:
        executable = install_browser(force=force)
        typer.echo(f"Browser executable: {executable}")
        _verify(executable)
    except (BrowserSetupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


@app.command("check")
def check(
    executable_path: str = typer.Option(
        "", "--executable-path", help="Chrome/Chromium executable to check; empty uses automatic discovery.",
    ),
) -> None:
    """Check browser automation without installing packages or changing login profiles."""
    try:
        _verify(executable_path)
    except (BrowserSetupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
