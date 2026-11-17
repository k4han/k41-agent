"""Resolve external CLI launches without managing their installation or credentials."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from agent.modules.acp.agents import ANTIGRAVITY_CLI_AGENT_ID
from agent.modules.acp.errors import AcpError, AcpNotInstalled
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
    if agent_id != ANTIGRAVITY_CLI_AGENT_ID:
        raise AcpError(f"Unknown ACP agent: {agent_id}.")
    context = [PROMPT_POLICY_VERSION, os.path.normcase(str(Path(cwd or Path.home()).resolve())), effective_native_tools(native_tools)]
    digest = hashlib.sha256(json.dumps(context, ensure_ascii=True).encode()).hexdigest()
    return f"{agent_id}:{digest}"


def cli_environment() -> dict[str, str]:
    """Retain the user's explicit configuration root without inheriting unrelated secrets."""
    return {"GEMINI_HOME": os.environ["GEMINI_HOME"]} if os.environ.get("GEMINI_HOME") else {}


@lru_cache(maxsize=16)
def _inspect_cli(executable: str, mtime_ns: int, size: int) -> tuple[str | None, str | None]:
    try:
        options = dict(
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=build_agent_env(cli_environment()), timeout=10, **hidden_subprocess_kwargs(),
        )
        version = subprocess.run([executable, "--version"], **options)
        help_result = subprocess.run([executable, "acp", "--help"], **options)
        help_text = help_result.stdout.decode("utf-8", "replace") + help_result.stderr.decode("utf-8", "replace")
        if version.returncode or help_result.returncode or not all(
            flag in help_text for flag in ("--agent", "--process-per-session")
        ):
            return None, "The installed agy does not support the required ACP flags. Run agy update, then refresh."
        return version.stdout.decode("utf-8", "replace").strip()[:128] or None, None
    except (OSError, subprocess.TimeoutExpired):
        return None, "Could not check agy. Confirm that agy acp --help works on the machine running K41, then refresh."


def cli_details(*, refresh: bool = False) -> dict:
    executable = shutil.which("agy")
    if not executable:
        return {"executable_path": None, "version": None, "error": None}
    executable = str(Path(executable).resolve())
    if refresh:
        _inspect_cli.cache_clear()
    try:
        info = Path(executable).stat()
        version, error = _inspect_cli(executable, info.st_mtime_ns, info.st_size)
    except OSError:
        version, error = None, "The agy executable is no longer available. Check PATH, then refresh."
    return {"executable_path": executable, "version": version, "error": error}


def managed_profile(native_tools: list[str] | None = None) -> Path:
    """Publish a K41-owned profile without changing the user's AGY configuration."""
    from agent.modules.acp.service import acp_data_root

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


def resolve_cli_command(*, cwd: str | None = None, native_tools: list[str] | None = None) -> AgentCommand:
    details = cli_details()
    if not details["executable_path"]:
        raise AcpNotInstalled("Antigravity CLI is not installed. Install agy on the machine running K41 and add it to PATH, then retry.")
    if details["error"]:
        raise AcpError(details["error"])
    version = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", details.get("version") or "")
    if version is None or tuple(map(int, version.groups())) < (1, 3, 3):
        raise AcpError("Managed ACP prompts and tools require agy 1.3.3 or newer. Run agy update, then refresh.")
    workspace = Path(cwd or Path.home()).resolve()
    if not workspace.is_dir():
        raise AcpError("The ACP working directory does not exist or is not a directory.")
    argv = [details["executable_path"], "acp", "--process-per-session"]
    profile = managed_profile(native_tools)
    argv.extend(["--agent", profile.stem, "--add-dir", str(profile.parent.parent.parent), "--include-default-customizations=false"])
    return AgentCommand(argv=argv, cwd=workspace, env=cli_environment())
