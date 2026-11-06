"""Discover system browsers and the Chromium installed for ARM64 hosts."""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def managed_browser_path() -> Path:
    return Path.home() / ".k41-agent" / "browser" / "chromium"


def browser_major_version(executable: str | None) -> str | None:
    """Read the browser's major version without starting a browser session."""
    if not executable:
        return None
    try:
        if sys.platform == "win32":
            # chrome.exe has no --version output; it sits beside a versioned directory or manifest.
            versions = [match.groups() for entry in Path(executable).parent.iterdir()
                        if (match := re.match(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", entry.name))]
            return max(versions, key=lambda parts: tuple(map(int, parts)))[0] if versions else None
        output = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.\d+\.\d+\.\d+", output)
    return match.group(1) if match else None


def desktop_user_agent(major_version: str) -> str:
    """Build the user agent this host's browser reports when it has a window."""
    if sys.platform == "win32":
        system = "Windows NT 10.0; Win64; x64"
    elif sys.platform == "darwin":
        system = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        system = "X11; Linux x86_64"
    return (f"Mozilla/5.0 ({system}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major_version}.0.0.0 Safari/537.36")


def headless_user_agent_args(executable: str | None) -> list[str]:
    """Replace the "HeadlessChrome" user agent, which Google's sign-in rejects before checking credentials."""
    major_version = browser_major_version(executable)
    if not major_version:
        logger.warning("Could not read the browser version; headless sign-in may be rejected by some sites.")
        return []
    return [f"--user-agent={desktop_user_agent(major_version)}"]


def find_browser() -> str | None:
    """Find Chrome or Chromium, respecting the operator's environment override."""
    candidates = [os.environ.get("CHROME_PATH", "")]
    if sys.platform == "win32":
        for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            if root := os.environ.get(name):
                candidates.extend(
                    str(Path(root) / "Google" / product / "Application" / "chrome.exe")
                    for product in ("Chrome SxS", "Chrome")
                )
    elif sys.platform == "darwin":
        candidates.extend(
            f"/Applications/{product}.app/Contents/MacOS/{product}"
            for product in ("Google Chrome Canary", "Google Chrome", "Google Chrome Beta", "Chromium")
        )
    else:
        candidates.append(str(managed_browser_path()))
        candidates.extend(
            path for name in ("google-chrome-stable", "google-chrome", "chromium-browser", "chromium")
            if (path := shutil.which(name))
        )
    return next((path for path in candidates if path and Path(path).is_file()
                 and (sys.platform == "win32" or os.access(path, os.X_OK))), None)
