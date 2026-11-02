"""Discover system browsers and the Chromium installed for ARM64 hosts."""

import os
import shutil
import sys
from pathlib import Path


def managed_browser_path() -> Path:
    return Path.home() / ".k41-agent" / "browser" / "chromium"


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
