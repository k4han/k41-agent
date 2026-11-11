"""Instabox desktop primitives: install and start, shell input, screenshots and the advisory lock.

Human control, per-machine serialization of agent input and viewer tickets
are backend-neutral and live in ``agent.modules.workspaces.computer``.
Instabox has an advisory control lock (``takeover``/``handback``) that tools
driving the display directly (``xdotool``) do not consult, so the shared
layer keeps its own record and mirrors it here.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from typing import Any

from agent.modules.instabox.client import redact
from agent.modules.instabox.config import DEFAULT_DESKTOP_HEIGHT, DEFAULT_DESKTOP_WIDTH

INSTALL_TIMEOUT_SECONDS = 300.0
INSTALL_POLL_SECONDS = 3.0
DISPLAY_PREFIX = 'export DISPLAY="${DESKTOP_DISPLAY:-:1}"; '
SCREENSHOT_PATH = "/tmp/k41-instabox-screenshot.png"


class DesktopService:
    def __init__(self, service: Any, *, clock: Any = None) -> None:
        self.service = service
        self._clock = clock or time.monotonic
        self._ready: set[str] = set()

    async def status(self, machine_id: str) -> dict[str, Any]:
        client = await self.service.client()
        return redact(dict(await client.desktop(machine_id, "status") or {}))

    async def ensure_started(
        self, machine_id: str, *, width: int = DEFAULT_DESKTOP_WIDTH, height: int = DEFAULT_DESKTOP_HEIGHT
    ) -> dict[str, Any]:
        """Install the desktop when the template lacks one, then start it."""
        client = await self.service.client()
        state = await client.desktop(machine_id, "status")
        if state.get("running") and machine_id in self._ready:
            return redact(dict(state))
        if not state.get("installed"):
            if state.get("failed"):
                raise RuntimeError(f"Desktop installation failed: {redact(state.get('log') or state.get('hint') or '')}")
            if not state.get("installing"):
                state = await client.desktop(machine_id, "install")
            deadline = self._clock() + INSTALL_TIMEOUT_SECONDS
            while not state.get("installed"):
                if state.get("failed"):
                    raise RuntimeError(f"Desktop installation failed: {redact(state.get('log') or '')}")
                if self._clock() > deadline:
                    raise TimeoutError("Desktop installation did not finish in time.")
                await asyncio.sleep(INSTALL_POLL_SECONDS)
                state = await client.desktop(machine_id, "status")
        if not state.get("running"):
            state = await client.desktop(machine_id, "start", width=int(width), height=int(height))
            if state.get("installed") is False:
                raise RuntimeError(str(state.get("hint") or "Desktop is not installed."))
        self._ready.add(machine_id)
        return redact(dict(state))

    async def set_control(self, machine_id: str, human: bool) -> dict[str, Any]:
        """Take over (and renew) or hand back the Instabox-side control lock."""
        client = await self.service.client()
        state = await client.desktop(machine_id, "takeover" if human else "handback")
        return redact(dict(state or {}))

    async def run(self, machine_id: str, command: str, *, envs: dict[str, str] | None = None,
                  timeout: int = 30) -> str:
        """Run one shell command against the display, starting the desktop first when needed."""
        async with self.service.machine_lock(machine_id):
            if machine_id not in self._ready:
                await self.ensure_started(machine_id)
            client = await self.service.client()
            result = await client.exec(machine_id, cmd=DISPLAY_PREFIX + command, envs=envs, timeout_seconds=timeout)
        exit_code = int(result.get("exitCode") or 0)
        if exit_code != 0:
            detail = (result.get("stderr") or result.get("stdout") or "").strip()
            if exit_code == 124 or "timed out" in detail.lower():
                raise TimeoutError(f"Desktop command timed out after {timeout}s.")
            raise RuntimeError(f"Desktop command failed ({exit_code}): {redact(detail)[:500]}")
        return str(result.get("stdout") or "")

    async def screenshot(self, machine_id: str) -> bytes:
        path = shlex.quote(SCREENSHOT_PATH)
        await self.run(
            machine_id,
            f"(import -window root {path} 2>/dev/null || scrot -o {path} 2>/dev/null "
            f"|| xwd -root -silent | convert xwd:- {path})",
            timeout=60,
        )
        client = await self.service.client()
        return await client.read_file(machine_id, SCREENSHOT_PATH)

    def invalidate(self, machine_id: str) -> None:
        """Forget that the desktop runs; the next input starts it again."""
        self._ready.discard(machine_id)

    async def close(self) -> None:
        self._ready.clear()


__all__ = ["DesktopService"]
