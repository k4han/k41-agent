"""Coordinate ACP installation operations with the running service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent.modules.acp.auth import AcpAuthManager
from agent.modules.acp.errors import AcpConflict, AcpError
from agent.modules.acp.installations import (
    OPENCODE_PACKAGES,
    Installation,
    discover_installation,
    managed_executable,
    package_is_installed,
    run_package_manager,
    version_number,
)
from agent.modules.acp.launcher import inspect_executable, invalidate_cli_details
from agent.modules.acp.releases import (
    download_executable,
    latest_release,
    platform_target,
)

logger = logging.getLogger(__name__)
Action = Literal["install", "update", "uninstall"]
LATEST_TTL_SECONDS = 300


def _package_latest(installation: Installation, *, stop: threading.Event | None = None) -> str:
    package, manager = installation.package, installation.manager
    versions = json.loads(run_package_manager(
        manager, "view", f"{package}@{OPENCODE_PACKAGES[package]}", "version", "--json", stop=stop,
    ))
    versions = [versions] if isinstance(versions, str) else versions
    if not isinstance(versions, list):
        raise AcpError("The package manager did not provide an OpenCode version.")
    stable = [value for value in versions if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value)]
    stable = [value for value in stable if int(value.split(".", 1)[0]) == OPENCODE_PACKAGES[package]]
    if not stable:
        raise AcpError("The package manager did not provide a stable OpenCode version.")
    return max(stable, key=version_number)


def _ensure_current(agent_id: str, revision: str, stop: threading.Event) -> Installation:
    if stop.is_set():
        raise AcpError("The ACP operation was interrupted.")
    current = discover_installation(agent_id)
    if current.revision() != revision:
        raise AcpConflict("The ACP installation changed. Refresh its status and try again.")
    return current


def installation_actions(installation: Installation) -> dict[str, bool]:
    platform_supported = platform_target() is not None
    return {
        "install": installation.path is None and platform_supported,
        "update": installation.manageable and (installation.package is not None or platform_supported),
        "uninstall": installation.manageable,
    }


def _change_package(agent_id: str, action: Action, installation: Installation, revision: str, stop: threading.Event) -> str | None:
    manager, package = installation.manager, installation.package
    if action == "uninstall":
        _ensure_current(agent_id, revision, stop)
        command = "uninstall" if installation.source == "npm" else "remove"
        run_package_manager(manager, command, "--global", package, stop=stop)
        if package_is_installed(installation):
            raise AcpError("The package manager left the selected OpenCode installation in place.")
        return None

    try:
        latest = _package_latest(installation, stop=stop)
    except (ValueError, OSError):
        raise AcpError("Could not check the latest OpenCode package version.") from None
    current = version_number(installation.package_version)
    if current is not None and current > version_number(latest):
        return latest
    if current == version_number(latest):
        _, error = inspect_executable(agent_id, installation.path.resolve(), refresh=True)
        if not error:
            return latest
    _ensure_current(agent_id, revision, stop)
    command = "install" if installation.source == "npm" else "add"
    run_package_manager(manager, command, "--global", f"{package}@{latest}", stop=stop)
    updated = discover_installation(agent_id)
    if updated.source != installation.source or updated.package != package or updated.package_version != latest:
        raise AcpError("Could not verify the updated OpenCode package. Check the global installation, then refresh.")
    version, error = inspect_executable(agent_id, updated.path.resolve(), refresh=True)
    if error or version_number(version) != version_number(latest):
        raise AcpError(error or "The updated OpenCode executable reported an unexpected version.")
    return latest


def apply_operation(agent_id: str, action: Action, revision: str, stop: threading.Event) -> str | None:
    installation = _ensure_current(agent_id, revision, stop)
    if not installation_actions(installation)[action]:
        raise AcpError("This operation is unavailable for the selected ACP installation.")
    if installation.source in {"npm", "pnpm"}:
        return _change_package(agent_id, action, installation, revision, stop)
    if action == "uninstall":
        installation.path.unlink()
        return None

    release = latest_release(agent_id)
    destination = installation.path if action == "update" else managed_executable(agent_id)
    if action == "update":
        reported, error = inspect_executable(agent_id, destination, refresh=True)
        current = version_number(reported)
        latest = version_number(release.version)
        if current and (current > latest or (current == latest and not error)):
            return release.version
    if destination.parent.resolve() != destination.parent:
        raise AcpError("The ACP installation directory resolves outside its expected location.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".acp-download-", dir=destination.parent) as staging:
        candidate = Path(staging) / destination.name
        download_executable(release, candidate, stop)
        reported, error = inspect_executable(agent_id, candidate)
        if error or version_number(reported) != version_number(release.version):
            raise AcpError(error or "The downloaded executable reported an unexpected version.")
        _ensure_current(agent_id, revision, stop)
        if destination.parent.resolve() != destination.parent or destination.is_symlink():
            raise AcpConflict("The ACP installation directory changed during the download.")
        os.replace(candidate, destination)
    return release.version


@dataclass
class Operation:
    id: str
    action: Action
    before: Installation
    revision: str
    version: str | None
    stop: threading.Event
    state: Literal["running", "succeeded", "failed"] = "running"
    error: str | None = None

    def payload(self) -> dict:
        return {"id": self.id, "action": self.action, "state": self.state, "error": self.error}


class AcpInstallManager:
    def __init__(self, auth: AcpAuthManager) -> None:
        self.auth = auth
        self.service = auth.service
        self._operations: dict[str, Operation] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._discovery: dict[str, tuple[float, Installation, str]] = {}
        self._latest: dict[tuple, tuple[float, str | None]] = {}

    def operation(self, agent_id: str) -> Operation | None:
        return self._operations.get(agent_id)

    def installation(self, agent_id: str, *, refresh: bool) -> tuple[Installation, str]:
        cached = self._discovery.get(agent_id)
        if cached and not refresh:
            checked, installation, revision = cached
            if time.monotonic() - checked < 30 and installation.is_current(agent_id, revision):
                return installation, revision
        installation = discover_installation(agent_id)
        revision = installation.revision()
        self._discovery[agent_id] = (time.monotonic(), installation, revision)
        return installation, revision

    def latest(self, agent_id: str, installation: Installation, *, check: bool, refresh: bool = False) -> str | None:
        if installation.source == "unknown":
            return None
        key = self._latest_key(agent_id, installation)
        cached = self._latest.get(key)
        if not check or (cached and not refresh and time.monotonic() - cached[0] < LATEST_TTL_SECONDS):
            return cached[1] if cached else None
        try:
            version = (_package_latest(installation) if installation.package else latest_release(agent_id).version)
        except (AcpError, OSError, ValueError):
            version = cached[1] if cached else None
        self._latest[key] = (time.monotonic(), version)
        return version

    @staticmethod
    def _latest_key(agent_id: str, installation: Installation) -> tuple:
        return agent_id, installation.manager, installation.package

    async def start(self, agent_id: str, action: Action, revision: str) -> Operation:
        with self.service.inspect_agent(agent_id):
            installation, current_revision = await asyncio.to_thread(self.installation, agent_id, refresh=True)
            if current_revision != revision:
                raise AcpConflict("The ACP installation changed. Refresh its status and try again.")
            if not installation_actions(installation)[action]:
                raise AcpError("This operation is unavailable for the selected ACP installation.")
            version = None
            if installation.path is not None and not self.service.agent_is_busy(agent_id):
                version, _ = await asyncio.to_thread(inspect_executable, agent_id, installation.path.resolve())
        self.service.begin_maintenance(agent_id)
        try:
            operation = Operation(uuid.uuid4().hex, action, installation, revision, version, threading.Event())
            self._operations[agent_id] = operation
            task = asyncio.create_task(self._run(agent_id, operation))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return operation
        except BaseException:
            self.service.end_maintenance(agent_id)
            raise

    async def _run(self, agent_id: str, operation: Operation) -> None:
        try:
            await self.service.restart_client(agent_id)
            version = await asyncio.to_thread(apply_operation, agent_id, operation.action, operation.revision, operation.stop)
            if version is not None:
                self._latest[self._latest_key(agent_id, operation.before)] = (time.monotonic(), version)
            operation.state = "succeeded"
        except AcpError as exc:
            operation.state, operation.error = "failed", str(exc)
        except OSError:
            operation.state, operation.error = "failed", "Could not modify the ACP installation. Check file access and retry."
        except Exception:
            logger.exception("ACP installation operation failed for %s", agent_id)
            operation.state, operation.error = "failed", "The ACP operation failed unexpectedly."
        finally:
            invalidate_cli_details()
            self.auth.invalidate(agent_id)
            self._discovery.pop(agent_id, None)
            self.service.end_maintenance(agent_id)

    async def close(self) -> None:
        for operation in self._operations.values():
            if operation.state == "running":
                operation.stop.set()
        await asyncio.gather(*self._tasks, return_exceptions=True)
