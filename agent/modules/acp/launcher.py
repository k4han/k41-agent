"""Inspect ACP executables and resolve commands for local workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.modules.acp.agents import ANTIGRAVITY_CLI_AGENT_ID, OPENCODE_AGENT_ID
from agent.modules.acp.errors import AcpError, AcpNotInstalled
from agent.modules.acp.installations import acp_data_root, find_cli, version_number
from agent.modules.acp.native_tools import effective_native_tools
from agent.modules.acp.process import AgentCommand, build_agent_env
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

PROMPT_POLICY_VERSION = 2


def normalize_agent_name(value: str | None) -> str | None:
    name = (value or "").strip()
    if name and (len(name) > 128 or name.startswith("-") or any(ord(char) < 32 for char in name)):
        raise ValueError("Agent names must be at most 128 characters and cannot contain control characters or start with '-'.")
    return name or None


def runtime_key(agent_id: str, cwd: str | None = None, *, native_tools: list[str] | None = None) -> str:
    """Keep CLI processes isolated by workspace, tools and managed prompt policy."""
    if agent_id == ANTIGRAVITY_CLI_AGENT_ID:
        context = [PROMPT_POLICY_VERSION, os.path.normcase(str(Path(cwd or Path.home()).resolve())), effective_native_tools(native_tools)]
    elif agent_id == OPENCODE_AGENT_ID:
        context = [PROMPT_POLICY_VERSION, os.path.normcase(str(Path(cwd or Path.home()).resolve()))]
    else:
        raise AcpError(f"Unknown ACP agent: {agent_id}.")
    digest = hashlib.sha256(json.dumps(context, ensure_ascii=True).encode()).hexdigest()
    return f"{agent_id}:{digest}"


def cli_environment(*, disable_agy_update: bool = False) -> dict[str, str]:
    """Retain the user's explicit configuration root without inheriting unrelated secrets."""
    env = {"GEMINI_HOME": os.environ["GEMINI_HOME"]} if os.environ.get("GEMINI_HOME") else {}
    if disable_agy_update:
        env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    return env


@lru_cache(maxsize=16)
def _inspect_cli(executable: str, identity: tuple[int, int, int, int]) -> tuple[str | None, str | None]:
    reported = None
    try:
        options = dict(
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=build_agent_env(cli_environment(disable_agy_update=True)), timeout=10, **hidden_subprocess_kwargs(),
        )
        version = subprocess.run([executable, "--version"], check=False, **options)
        if version.returncode == 0:
            reported = version.stdout.decode("utf-8", "replace").strip()[:128] or None
        help_result = subprocess.run([executable, "acp", "--help"], check=False, **options)
        help_text = help_result.stdout.decode("utf-8", "replace") + help_result.stderr.decode("utf-8", "replace")
        if version.returncode or help_result.returncode or not all(
            flag in help_text for flag in ("--agent", "--process-per-session")
        ):
            return reported, "The installed agy does not support the required ACP flags. Run agy update, then refresh."
        return reported, None
    except (OSError, subprocess.TimeoutExpired):
        return reported, "Could not check agy. Confirm that agy acp --help works on the machine running K41, then refresh."


@lru_cache(maxsize=16)
def _inspect_opencode_cli(executable: str, identity: tuple[int, int, int, int]) -> tuple[str | None, str | None]:
    reported = None
    try:
        options = dict(
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=build_agent_env(cli_environment()), timeout=10, **hidden_subprocess_kwargs(),
        )
        version = subprocess.run([executable, "--version"], check=False, **options)
        if version.returncode == 0:
            reported = version.stdout.decode("utf-8", "replace").strip()[:128] or None
        help_result = subprocess.run([executable, "acp", "--help"], check=False, **options)
        if version.returncode or help_result.returncode:
            return reported, "The installed opencode does not support the ACP command. Update opencode, then refresh."
        return reported, None
    except (OSError, subprocess.TimeoutExpired):
        return reported, "Could not check opencode. Confirm that opencode acp --help works on the machine running K41, then refresh."


def invalidate_cli_details() -> None:
    _inspect_cli.cache_clear()
    _inspect_opencode_cli.cache_clear()


def inspect_executable(agent_id: str, executable: Path, *, refresh: bool = False) -> tuple[str | None, str | None]:
    if agent_id == ANTIGRAVITY_CLI_AGENT_ID:
        inspect_fn = _inspect_cli
        missing_msg = "The agy executable is no longer available. Check PATH, then refresh."
    elif agent_id == OPENCODE_AGENT_ID:
        inspect_fn = _inspect_opencode_cli
        missing_msg = "The opencode executable is no longer available. Check PATH, then refresh."
    else:
        raise AcpError(f"Unknown ACP agent: {agent_id}.")

    if refresh:
        inspect_fn.cache_clear()
    try:
        info = executable.stat()
        return inspect_fn(str(executable), (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
    except OSError:
        return None, missing_msg


def cli_details(agent_id: str = ANTIGRAVITY_CLI_AGENT_ID, *, refresh: bool = False) -> dict[str, Any]:
    try:
        path, managed = find_cli(agent_id)
    except KeyError:
        raise AcpError(f"Unknown ACP agent: {agent_id}.") from None
    if path is None:
        return {"executable_path": None, "version": None, "error": None}
    executable = path.resolve()
    version, error = inspect_executable(agent_id, executable, refresh=refresh)
    return {"executable_path": str(executable), "version": version, "error": error, "managed": managed}


def managed_profile(native_tools: list[str] | None = None) -> Path:
    """Publish a K41-owned profile without changing the user's AGY configuration."""
    root = (acp_data_root() / ANTIGRAVITY_CLI_AGENT_ID / "profiles").resolve()
    tools = effective_native_tools(native_tools)
    identity = json.dumps([str(root), PROMPT_POLICY_VERSION, tools])
    name = "k41-card-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    path = root / ".agents" / "agents" / f"{name}.md"
    content = (
        f"---\nname: {name}\ndescription: Use the K41 card's instructions and tools.\n"
        "mainAgent: true\nsubagent: false\nexcludeDefaultComponents: true\n"
        f"inheritCustomizations: false\ninheritMcp: false\ntools: {json.dumps(tools)}\n---\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return path.resolve()
    # Different runtime contexts can start concurrently. Workers must see a complete file.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.resolve()


def resolve_cli_command(agent_id: str = ANTIGRAVITY_CLI_AGENT_ID, *, cwd: str | None = None, native_tools: list[str] | None = None) -> AgentCommand:
    if agent_id == ANTIGRAVITY_CLI_AGENT_ID:
        details = cli_details()
        if not details["executable_path"]:
            raise AcpNotInstalled("Antigravity CLI is not installed. Install it in Settings > ACP Agents or add agy to PATH, then retry.")
        if details["error"]:
            raise AcpError(details["error"])
        version = version_number(details.get("version"))
        if version is None or version[:3] < (1, 3, 3):
            raise AcpError("Managed ACP prompts and tools require agy 1.3.3 or newer. Run agy update, then refresh.")
        workspace = Path(cwd or Path.home()).resolve()
        if not workspace.is_dir():
            raise AcpError("The ACP working directory does not exist or is not a directory.")
        argv = [details["executable_path"], "acp", "--process-per-session"]
        profile = managed_profile(native_tools)
        argv.extend(["--agent", profile.stem, "--add-dir", str(profile.parent.parent.parent), "--include-default-customizations=false"])
        return AgentCommand(argv=argv, cwd=workspace, env=cli_environment(disable_agy_update=bool(details.get("managed"))))
    elif agent_id == OPENCODE_AGENT_ID:
        details = cli_details(OPENCODE_AGENT_ID)
        if not details["executable_path"]:
            raise AcpNotInstalled("OpenCode CLI is not installed. Install it in Settings > ACP Agents or add opencode to PATH, then retry.")
        if details["error"]:
            raise AcpError(details["error"])
        workspace = Path(cwd or Path.home()).resolve()
        if not workspace.is_dir():
            raise AcpError("The ACP working directory does not exist or is not a directory.")
        argv = [details["executable_path"], "acp"]
        return AgentCommand(argv=argv, cwd=workspace, env=cli_environment())
    else:
        raise AcpError(f"Unknown ACP agent: {agent_id}.")
