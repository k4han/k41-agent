"""ACP Registry lookups, reduced to what this platform can install."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

import httpx

from agent.modules.acp.agents import AcpAgentDefinition
from agent.modules.acp.errors import AcpError

REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MACHINE_NAMES = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
_SYSTEM_NAMES = {"windows": "windows", "linux": "linux", "darwin": "darwin"}


@dataclass(frozen=True)
class AgentRelease:
    agent_id: str
    version: str
    license: str
    archive_url: str
    sha256: str
    command: str
    args: tuple[str, ...]


def current_target() -> str:
    system = _SYSTEM_NAMES.get(platform.system().lower())
    machine = _MACHINE_NAMES.get(platform.machine().lower())
    if not system or not machine:
        raise AcpError(f"This platform is not supported: {platform.system()} {platform.machine()}.")
    return f"{system}-{machine}"


def _validated_command(command: Any) -> str:
    """The command comes from the network and is later executed, so it stays inside the install."""
    if not isinstance(command, str) or not command.strip():
        raise AcpError("The registry entry has no command.")
    normalized = command.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(command).drive or any(part in {"", ".", ".."} for part in parts):
        raise AcpError("The registry entry has an unsafe command path.")
    return normalized


def parse_release(entry: dict[str, Any], definition: AcpAgentDefinition, target: str) -> AgentRelease:
    binary = (entry.get("distribution") or {}).get("binary") or {}
    selected = binary.get(target)
    if not isinstance(selected, dict):
        raise AcpError(f"{definition.display_name} has no release for {target}.")
    url = str(selected.get("archive") or "")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in definition.allowed_download_hosts or parts.username or parts.password:
        raise AcpError(f"Refusing a {definition.display_name} download from an unexpected location.")
    sha256 = str(selected.get("sha256") or "").lower()
    if not _SHA256.fullmatch(sha256):
        raise AcpError("The registry entry has no valid sha256 checksum.")
    args = selected.get("args") or []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise AcpError("The registry entry has invalid arguments.")
    version = str(entry.get("version") or "")
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}", version):
        raise AcpError("The registry entry has an invalid version.")
    return AgentRelease(
        agent_id=definition.id,
        version=version,
        license=str(entry.get("license") or "unknown"),
        archive_url=url,
        sha256=sha256,
        command=_validated_command(selected.get("cmd")),
        args=tuple(args),
    )


def fetch_release(definition: AcpAgentDefinition, *, url: str = REGISTRY_URL, target: str | None = None) -> AgentRelease:
    """Blocking; call from a worker thread."""
    try:
        response = httpx.get(url, timeout=30, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AcpError(f"Could not read the ACP Registry: {type(exc).__name__}.") from exc
    agents = payload.get("agents") if isinstance(payload, dict) else None
    entry = next((item for item in agents or [] if isinstance(item, dict) and item.get("id") == definition.id), None)
    if entry is None:
        raise AcpError(f"{definition.display_name} is not listed in the ACP Registry.")
    return parse_release(entry, definition, target or current_target())
