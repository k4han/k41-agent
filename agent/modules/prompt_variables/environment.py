"""Shared host and shell environment detection for system prompt variables.

Single source of truth for ``host_os``, ``shell_name`` and ``shell_kind``.
Both ``prompt_builders.get_system_default_variables`` and
``PromptVariableService.list_variables`` read from here so the values stay
consistent. Shell detection mirrors ``run_bash_tool._resolve_shell`` without
importing it to avoid heavy/circular imports.
"""

from __future__ import annotations

import os
import shutil
import sys


def get_host_os(platform_name: str | None = None) -> str:
    """Return normalized host OS name: ``windows``, ``macos`` or ``linux``."""
    name = platform_name if platform_name is not None else sys.platform
    if name == "win32":
        return "windows"
    if name == "darwin":
        return "macos"
    return name


def get_shell_name(
    k41_shell: str | None = None,
    *,
    platform_name: str | None = None,
    path_lookup=shutil.which,
) -> str:
    """Return resolved shell executable name for local ``bash``/``run_bash`` tools.

    ``K41_SHELL`` wins when set; otherwise auto-detect the best available
    shell. Returns a short name such as ``pwsh.exe``, ``cmd.exe``, ``bash``
    or ``sh`` (basename of the resolved executable, lowercased on Windows).
    """
    override = k41_shell if k41_shell is not None else os.environ.get("K41_SHELL", "")
    override = (override or "").strip()
    if override:
        base = os.path.basename(override)
        return base.lower() if (platform_name or sys.platform) == "win32" else base
    system = platform_name if platform_name is not None else sys.platform
    is_windows = system == "win32"
    if is_windows:
        if path_lookup("pwsh.exe") or path_lookup("pwsh"):
            return "pwsh.exe"
        if path_lookup("powershell.exe") or path_lookup("powershell"):
            return "powershell.exe"
        default = os.environ.get("COMSPEC", "cmd.exe") or "cmd.exe"
        return os.path.basename(default).lower()
    if path_lookup("bash"):
        return "bash"
    if path_lookup("sh"):
        return "sh"
    return "sh"


def get_shell_kind(shell_name: str | None = None) -> str:
    """Return shell syntax family: ``powershell``, ``cmd`` or ``posix``."""
    name = (shell_name or get_shell_name()).strip().lower()
    base = os.path.basename(name)
    stem = base.removesuffix(".exe")
    if stem in {"pwsh", "powershell"}:
        return "powershell"
    if stem == "cmd":
        return "cmd"
    return "posix"


def get_environment_defaults() -> dict[str, str]:
    """Return default ``host_os``/``shell_name``/``shell_kind`` values."""
    shell_name = get_shell_name()
    return {
        "host_os": get_host_os(),
        "shell_name": shell_name,
        "shell_kind": get_shell_kind(shell_name),
    }


__all__ = [
    "get_environment_defaults",
    "get_host_os",
    "get_shell_kind",
    "get_shell_name",
]
