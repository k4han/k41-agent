"""Persist update progress outside the source tree and serialize installers."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import psutil

from agent.bootstrap.process_utils import get_process_cmdline, is_process_alive

QUEUED_TIMEOUT_SECONDS = 60.0
logger = logging.getLogger(__name__)


class UpdateBusyError(RuntimeError):
    pass


@contextmanager
def update_lock(agent_home: Path) -> Iterator[None]:
    agent_home.mkdir(parents=True, exist_ok=True)
    with (agent_home / "update.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateBusyError("Another update is already in progress.") from exc
        try:
            cleanup_stale_update_status_files(agent_home)
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cleanup_stale_update_status_files(agent_home: Path) -> None:
    """Remove abandoned status writes while the caller holds the update lock."""
    cutoff = time.time() - QUEUED_TIMEOUT_SECONDS
    for candidate in agent_home.glob("update-status-*.tmp"):
        if not re.fullmatch(r"update-status-[0-9a-f]{32}\.tmp", candidate.name):
            continue
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove stale update status file %s: %s", candidate, exc)


def get_process_start_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _is_update_process_running(status: dict[str, Any]) -> bool:
    pid = status.get("pid")
    if not isinstance(pid, int) or pid <= 0 or not is_process_alive(pid):
        return False
    expected_start = status.get("process_started_at")
    if isinstance(expected_start, (int, float)):
        actual_start = get_process_start_time(pid)
        if actual_start is not None:
            return actual_start == expected_start
        # Start time is unreadable (AccessDenied/zombie). Fall back to cmdline
        # so a reused PID by an unrelated process does not block updates forever.
        # Keep the guard conservative when cmdline is also unverifiable.
        command = get_process_cmdline(pid)
        if not command:
            return True
        return ("agent.bootstrap.cli" in command or "k41" in command) and bool(
            re.search(r"(?<![\w-])update(?![\w-])", command)
        )
    # Support records written before process creation times were recorded.
    command = get_process_cmdline(pid)
    if not command:
        return True
    return ("agent.bootstrap.cli" in command or "k41" in command) and bool(
        re.search(r"(?<![\w-])update(?![\w-])", command)
    )


def write_update_status(agent_home: Path, status: dict[str, Any]) -> None:
    destination = agent_home / "update-status.json"
    temporary = agent_home / f"update-status-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(status), encoding="utf-8")
        for attempt in range(100):
            try:
                temporary.replace(destination)
                break
            except PermissionError:
                # Windows readers can briefly hold a handle that prevents replacement.
                if attempt == 99:
                    raise
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


def read_update_status(agent_home: Path) -> dict[str, Any] | None:
    try:
        status = json.loads((agent_home / "update-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict):
        return None
    if status.get("status") == "queued":
        queued_at = status.get("queued_at")
        if not isinstance(queued_at, (int, float)) or time.time() - queued_at > QUEUED_TIMEOUT_SECONDS:
            return {**status, "status": "failed", "error": "The update process did not start. Check update.log."}
    if status.get("status") == "running":
        if not _is_update_process_running(status):
            return {**status, "status": "failed", "error": "The update process exited unexpectedly. Check update.log."}
    return status
