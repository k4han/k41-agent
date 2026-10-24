"""Portable environment detection and execution-scoped system prompt variables."""

from __future__ import annotations

import getpass
import os
import shutil
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.modules.workspaces import WorkspaceRef


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
    """Return resolved shell executable name for local ``bash`` tools.

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


def get_execution_environment(shell: str | None = None) -> dict[str, str]:
    """Describe the current process environment using the executing tool's shell."""
    environment = get_environment_defaults()
    if shell:
        shell_name = os.path.basename(shell)
        if sys.platform == "win32":
            shell_name = shell_name.lower()
        environment.update(shell_name=shell_name, shell_kind=get_shell_kind(shell_name))
    try:
        username = getpass.getuser()
    except Exception:
        username = "user"
    return {**environment, "operating_system": environment["host_os"], "user_name": username}


async def get_runtime_system_variables(workspace: WorkspaceRef, *, thread_id: str = "") -> dict[str, str]:
    """Read system variables from the same runtime that executes workspace tools."""
    from agent.modules.tools.coding.contracts import InvocationContext
    from agent.modules.tools.coding.service import get_coding_service

    service = get_coding_service()
    if workspace.backend == "local":
        environment = get_execution_environment(service.shell)
        working_dir = workspace.locator
    else:
        context = InvocationContext(
            agent_name="default", workspace=str(workspace.metadata.get("root") or ""),
            thread_id=thread_id, backend=workspace.backend, locator=workspace.locator,
        )
        client = await service.remote.client(context)
        await client.ensure_started()
        environment = await client.call({"operation": "environment"})
        working_dir = client.backend.root
    return {**environment, "workspace": workspace.display_label(), "working_dir": working_dir}


__all__ = [
    "get_environment_defaults",
    "get_execution_environment",
    "get_host_os",
    "get_shell_kind",
    "get_shell_name",
    "get_runtime_system_variables",
]
