"""Instabox desktop: start, human control, serialized agent input and VNC viewer tickets.

Instabox has an advisory control lock (``takeover``/``handback``), but tools that
drive the display directly (``xdotool``) do not consult it. This service keeps
its own per-machine controller record and refuses agent input while a human
holds control.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import shlex
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.modules.instabox.client import redact
from agent.modules.instabox.config import DEFAULT_DESKTOP_HEIGHT, DEFAULT_DESKTOP_WIDTH
from agent.modules.instabox.errors import InstaboxConflictError

logger = logging.getLogger(__name__)

CONTROL_TTL_SECONDS = 30.0
TICKET_TTL_SECONDS = 60.0
INSTALL_TIMEOUT_SECONDS = 300.0
INSTALL_POLL_SECONDS = 3.0
MAX_TYPE_CHARS = 4000
SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}
MOUSE_BUTTONS = {"left": 1, "middle": 2, "right": 3}
DISPLAY_PREFIX = 'export DISPLAY="${DESKTOP_DISPLAY:-:1}"; '
SCREENSHOT_PATH = "/tmp/k41-instabox-screenshot.png"


@dataclass
class DesktopController:
    owner_id: str
    expires_at: float


@dataclass
class ViewerTicket:
    machine_id: str
    owner_id: str
    expires_at: float


@dataclass
class DesktopSessions:
    viewers: dict[str, set[asyncio.Event]] = field(default_factory=dict)


class DesktopService:
    def __init__(self, service: Any, *, clock: Any = None) -> None:
        self.service = service
        self._clock = clock or time.monotonic
        self._controllers: dict[str, DesktopController] = {}
        self._tickets: dict[str, ViewerTicket] = {}
        self._closers: dict[str, set[asyncio.Event]] = {}
        self._ready: set[str] = set()

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def status(self, machine_id: str) -> dict[str, Any]:
        client = await self.service.client()
        state = await client.desktop(machine_id, "status")
        return self._state_payload(machine_id, state)

    def _state_payload(self, machine_id: str, state: dict[str, Any] | None) -> dict[str, Any]:
        controller = self.controller(machine_id)
        return {
            **redact(dict(state or {})),
            "app_controller": controller.owner_id if controller else None,
            "app_controller_expires_in": max(0.0, controller.expires_at - self._clock()) if controller else 0.0,
        }

    async def ensure_started(
        self, machine_id: str, *, width: int = DEFAULT_DESKTOP_WIDTH, height: int = DEFAULT_DESKTOP_HEIGHT
    ) -> dict[str, Any]:
        """Install the desktop when the template lacks one, then start it."""
        client = await self.service.client()
        state = await client.desktop(machine_id, "status")
        if state.get("running") and machine_id in self._ready:
            return self._state_payload(machine_id, state)
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
        return self._state_payload(machine_id, state)

    # ------------------------------------------------------------------ #
    #  Human control
    # ------------------------------------------------------------------ #

    def controller(self, machine_id: str) -> DesktopController | None:
        current = self._controllers.get(machine_id)
        if current is not None and current.expires_at <= self._clock():
            self._controllers.pop(machine_id, None)
            return None
        return current

    def human_in_control(self, machine_id: str) -> bool:
        return self.controller(machine_id) is not None

    async def take_control(self, machine_id: str, owner_id: str) -> dict[str, Any]:
        async with self.service.machine_lock(machine_id):
            current = self.controller(machine_id)
            if current is not None and current.owner_id != owner_id:
                raise InstaboxConflictError(
                    "Another user is controlling this desktop.", code="desktop_controlled_by_other"
                )
            self._controllers[machine_id] = DesktopController(owner_id, self._clock() + CONTROL_TTL_SECONDS)
            client = await self.service.client()
            state = await client.desktop(machine_id, "takeover")
            return self._state_payload(machine_id, state)

    async def heartbeat(self, machine_id: str, owner_id: str) -> dict[str, Any]:
        current = self.controller(machine_id)
        if current is None or current.owner_id != owner_id:
            raise InstaboxConflictError("You no longer control this desktop.", code="desktop_control_lost")
        current.expires_at = self._clock() + CONTROL_TTL_SECONDS
        client = await self.service.client()
        # Each call from the holder renews the Instabox-side lock as well.
        state = await client.desktop(machine_id, "takeover")
        return self._state_payload(machine_id, state)

    async def release_control(self, machine_id: str, owner_id: str) -> dict[str, Any]:
        current = self.controller(machine_id)
        if current is not None and current.owner_id == owner_id:
            self._controllers.pop(machine_id, None)
        client = await self.service.client()
        state = await client.desktop(machine_id, "handback")
        return self._state_payload(machine_id, state)

    # ------------------------------------------------------------------ #
    #  Agent input (serialized per machine)
    # ------------------------------------------------------------------ #

    async def _run(self, machine_id: str, command: str, *, envs: dict[str, str] | None = None, timeout: int = 30) -> str:
        if self.human_in_control(machine_id):
            raise InstaboxConflictError(
                "A person is controlling this desktop. Wait until they hand control back.",
                code="human_in_control",
            )
        client = await self.service.client()
        result = await client.exec(machine_id, cmd=DISPLAY_PREFIX + command, envs=envs, timeout_seconds=timeout)
        exit_code = int(result.get("exitCode") or 0)
        if exit_code != 0:
            detail = (result.get("stderr") or result.get("stdout") or "").strip()
            raise RuntimeError(f"Desktop command failed ({exit_code}): {redact(detail)[:500]}")
        return str(result.get("stdout") or "")

    async def act(self, machine_id: str, action: str, **values: Any) -> dict[str, Any]:
        async with self.service.machine_lock(machine_id):
            if machine_id not in self._ready:
                await self.ensure_started(machine_id)
            command, envs = self._command(action, values)
            await self._run(machine_id, command, envs=envs)
            return {"machine_id": machine_id, "action": action, "ok": True}

    async def screenshot(self, machine_id: str) -> bytes:
        async with self.service.machine_lock(machine_id):
            if machine_id not in self._ready:
                await self.ensure_started(machine_id)
            path = shlex.quote(SCREENSHOT_PATH)
            await self._run(
                machine_id,
                f"(import -window root {path} 2>/dev/null || scrot -o {path} 2>/dev/null "
                f"|| xwd -root -silent | convert xwd:- {path})",
                timeout=60,
            )
            client = await self.service.client()
            return await client.read_file(machine_id, SCREENSHOT_PATH)

    def _command(self, action: str, values: dict[str, Any]) -> tuple[str, dict[str, str] | None]:
        def coord(name: str) -> int:
            value = int(values[name])
            if value < 0 or value > 10000:
                raise ValueError(f"{name} is out of range.")
            return value

        if action == "move":
            return f"xdotool mousemove {coord('x')} {coord('y')}", None
        if action == "click":
            button = MOUSE_BUTTONS.get(str(values.get("button") or "left"))
            if button is None:
                raise ValueError("button must be left, middle or right.")
            repeat = "--repeat 2 " if values.get("double") else ""
            return f"xdotool mousemove {coord('x')} {coord('y')} click {repeat}{button}", None
        if action == "drag":
            return (
                f"xdotool mousemove {coord('x1')} {coord('y1')} mousedown 1 "
                f"mousemove {coord('x2')} {coord('y2')} mouseup 1"
            ), None
        if action == "scroll":
            button = SCROLL_BUTTONS.get(str(values.get("direction") or "down"))
            if button is None:
                raise ValueError("direction must be up, down, left or right.")
            amount = max(1, min(50, int(values.get("amount") or 3)))
            prefix = ""
            if values.get("x") is not None and values.get("y") is not None:
                prefix = f"xdotool mousemove {coord('x')} {coord('y')}; "
            return f"{prefix}xdotool click --repeat {amount} {button}", None
        if action == "type":
            text = str(values.get("text") or "")
            if not text or len(text) > MAX_TYPE_CHARS:
                raise ValueError(f"text must contain 1 to {MAX_TYPE_CHARS} characters.")
            return 'xdotool type --delay 12 -- "$K41_DESKTOP_TEXT"', {"K41_DESKTOP_TEXT": text}
        if action == "press":
            keys = values.get("keys")
            if isinstance(keys, str):
                keys = [keys]
            if not keys or not all(isinstance(key, str) and key.strip() for key in keys):
                raise ValueError("keys must name at least one key.")
            chord = "+".join(key.strip() for key in keys)
            return f"xdotool key -- {shlex.quote(chord)}", None
        if action == "open_url":
            url = str(values.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("url must start with http:// or https://.")
            return f"(nohup xdg-open {shlex.quote(url)} >/dev/null 2>&1 &)", None
        raise ValueError(f"Unsupported desktop action: {action}")

    # ------------------------------------------------------------------ #
    #  Viewer tickets and connections
    # ------------------------------------------------------------------ #

    def issue_ticket(self, machine_id: str, owner_id: str) -> str:
        now = self._clock()
        self._tickets = {key: value for key, value in self._tickets.items() if value.expires_at > now}
        token = secrets.token_urlsafe(32)
        self._tickets[token] = ViewerTicket(machine_id, owner_id, now + TICKET_TTL_SECONDS)
        return token

    def consume_ticket(self, token: str) -> ViewerTicket:
        ticket = self._tickets.pop(str(token or ""), None)
        if ticket is None or ticket.expires_at < self._clock():
            raise PermissionError("Viewer ticket is invalid or expired.")
        return ticket

    def register_viewer(self, machine_id: str) -> tuple[str, asyncio.Event]:
        closer = asyncio.Event()
        self._closers.setdefault(machine_id, set()).add(closer)
        return uuid.uuid4().hex, closer

    def unregister_viewer(self, machine_id: str, closer: asyncio.Event) -> None:
        closers = self._closers.get(machine_id)
        if closers is not None:
            closers.discard(closer)
            if not closers:
                self._closers.pop(machine_id, None)

    def invalidate(self, machine_id: str) -> None:
        """Drop cached state and close viewers after a restore or delete."""
        self._ready.discard(machine_id)
        self._controllers.pop(machine_id, None)
        for closer in list(self._closers.get(machine_id, ())):
            closer.set()

    async def close(self) -> None:
        for machine_id in list(self._closers):
            self.invalidate(machine_id)
        with contextlib.suppress(Exception):
            self._tickets.clear()


__all__ = ["CONTROL_TTL_SECONDS", "DesktopService", "TICKET_TTL_SECONDS"]
