"""Backend-neutral desktop control for workspace machines.

Backends only provide raw desktop primitives through ``WorkspaceComputer``.
Everything that must behave the same on every backend lives here: the action
vocabulary and its validation, key names, screenshot sizing, the human
control lock, per-machine serialization and viewer tickets.
"""

from __future__ import annotations

import asyncio
import io
import math
import re
import secrets
import shlex
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

# Action vocabulary ---------------------------------------------------------- #

# Action -> fields it requires. ``type`` needs ``text`` or ``secret`` (checked separately).
COMPUTER_ACTION_FIELDS: dict[str, tuple[str, ...]] = {
    "click": ("x", "y"),
    "double_click": ("x", "y"),
    "triple_click": ("x", "y"),
    "move": ("x", "y"),
    "drag": ("x", "y", "x2", "y2"),
    "scroll": (),
    "type": (),
    "key": ("keys",),
    "open_url": ("url",),
    "wait": (),
}
COMPUTER_ACTIONS = tuple(COMPUTER_ACTION_FIELDS)
# Fields that say what to do -> the only action that uses them. On any other action they mean
# the caller expected two inputs from one step, so the step is refused instead of half done.
INTENT_FIELDS = {"text": "type", "secret": "type", "keys": "key", "url": "open_url"}
CLICK_ACTIONS = frozenset({"click", "double_click", "triple_click"})
# Actions that accept held modifier keys.
POINTER_ACTIONS = CLICK_ACTIONS | {"move", "drag", "scroll"}
# Actions the shared layer handles itself; backends never receive them.
LOCAL_ACTIONS = frozenset({"wait"})
MOUSE_BUTTONS = ("left", "middle", "right")
SCROLL_DIRECTIONS = ("up", "down", "left", "right")
MAX_COORDINATE = 10000
MAX_SCROLL_AMOUNT = 50
DEFAULT_SCROLL_AMOUNT = 3
MAX_TYPE_CHARS = 4000
MAX_WAIT_SECONDS = 10.0
DEFAULT_WAIT_SECONDS = 1.0
SECRET_NAME_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]{0,254}")

# Key names ------------------------------------------------------------------ #

MODIFIER_KEYS = ("ctrl", "alt", "shift", "super")
NAMED_KEYS = frozenset({
    *MODIFIER_KEYS,
    "enter", "escape", "tab", "backspace", "delete", "insert", "space",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "capslock", "printscreen", "menu",
    *(f"f{index}" for index in range(1, 25)),
})
KEY_ALIASES = {
    "return": "enter", "esc": "escape", "control": "ctrl", "ctl": "ctrl",
    "cmd": "super", "command": "super", "meta": "super", "win": "super", "windows": "super",
    "option": "alt", "opt": "alt", "del": "delete", "bksp": "backspace", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "prior": "pageup", "next": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "spacebar": "space", "print": "printscreen", "prtsc": "printscreen",
}
KEY_NAMES_HINT = (
    "Use single characters or: enter, escape, tab, backspace, delete, insert, space, up, down, left, right, "
    "home, end, pageup, pagedown, capslock, printscreen, menu, f1-f24, and modifiers ctrl, alt, shift, super."
)


class ComputerError(RuntimeError):
    """A desktop operation failed in a way the agent or dashboard can act on.

    ``code`` is one of ``unsupported``, ``disabled``, ``human_in_control``,
    ``controlled_by_other``, ``control_lost``, ``invalid_input``,
    ``not_found``, ``permission_denied``, ``upstream`` or ``timeout``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_key(name: Any) -> str:
    """Return the canonical name of one key, or raise ``ComputerError``."""
    value = str(name or "")
    if len(value) == 1 and value.isprintable():
        return value
    key = value.strip().lower()
    for candidate in (key, key.replace("_", "").replace("-", "").replace(" ", "")):
        candidate = KEY_ALIASES.get(candidate, candidate)
        if candidate in NAMED_KEYS:
            return candidate
    raise ComputerError("invalid_input", f"Unknown key {value!r}. {KEY_NAMES_HINT}")


def normalize_keys(keys: Any) -> tuple[str, ...]:
    if isinstance(keys, str):
        keys = [keys]
    if not keys:
        raise ComputerError("invalid_input", "keys must name at least one key.")
    normalized = tuple(normalize_key(key) for key in keys)
    if len(normalized) > 1:
        # Shifted letters in chords are spelled with the shift modifier instead.
        normalized = tuple(key.lower() if len(key) == 1 and key.isalpha() else key for key in normalized)
    return normalized


# Steps ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class ComputerStep:
    """One validated desktop input. Coordinates are screen pixels once sent to a backend."""

    action: str
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    button: str = "left"
    direction: str = "down"
    amount: int = DEFAULT_SCROLL_AMOUNT
    text: str | None = None
    secret: str | None = None
    keys: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    url: str | None = None
    seconds: float = DEFAULT_WAIT_SECONDS

    @classmethod
    def parse(cls, values: Mapping[str, Any]) -> ComputerStep:
        """Validate one step.

        Unused settings (button, direction, coordinates, ...) are dropped; an unused
        ``INTENT_FIELDS`` value is an error.
        """
        action = str(values.get("action") or "")
        if action not in COMPUTER_ACTION_FIELDS:
            raise ComputerError("invalid_input", f"Unknown action {action!r}. Use one of: {', '.join(COMPUTER_ACTIONS)}.")

        def present(name: str) -> bool:
            return values.get(name) not in (None, "", [], ())

        missing = [name for name in COMPUTER_ACTION_FIELDS[action] if not present(name)]
        if missing:
            raise ComputerError("invalid_input", f"Action {action} requires: {', '.join(missing)}.")
        for name, owner in INTENT_FIELDS.items():
            if action != owner and present(name):
                raise ComputerError("invalid_input",
                                    f"Action {action} does not use {name}; add a separate {owner} step.")
        fields: dict[str, Any] = {"action": action}
        coordinates = COMPUTER_ACTION_FIELDS[action] if action != "scroll" else ("x", "y")
        if action == "scroll" and present("x") != present("y"):
            raise ComputerError("invalid_input", "Scroll at a position needs both x and y, or neither.")
        for name in ("x", "y", "x2", "y2"):
            if name in coordinates and present(name):
                fields[name] = _coordinate(name, values[name])
        if action in CLICK_ACTIONS or action == "drag":
            fields["button"] = _choice("button", values.get("button") or "left", MOUSE_BUTTONS)
        if action == "scroll":
            fields["direction"] = _choice("direction", values.get("direction") or "down", SCROLL_DIRECTIONS)
            fields["amount"] = _bounded_int("amount", values.get("amount") or DEFAULT_SCROLL_AMOUNT, 1, MAX_SCROLL_AMOUNT)
        if action in POINTER_ACTIONS and present("modifiers"):
            modifiers = normalize_keys(values["modifiers"])
            if any(key not in MODIFIER_KEYS for key in modifiers):
                raise ComputerError("invalid_input", f"modifiers may only contain {', '.join(MODIFIER_KEYS)}.")
            fields["modifiers"] = tuple(dict.fromkeys(modifiers))
        if action == "type":
            if present("text") == present("secret"):
                raise ComputerError("invalid_input", "Action type requires exactly one of text or secret.")
            if present("text"):
                text = str(values["text"])
                if len(text) > MAX_TYPE_CHARS:
                    raise ComputerError("invalid_input", f"text must contain 1 to {MAX_TYPE_CHARS} characters.")
                fields["text"] = text
            else:
                secret = str(values["secret"]).strip()
                if not SECRET_NAME_PATTERN.fullmatch(secret):
                    raise ComputerError("invalid_input", "secret must be the name of a project secret, e.g. GITHUB_PASSWORD.")
                fields["secret"] = secret
        if action == "key":
            fields["keys"] = normalize_keys(values["keys"])
        if action == "open_url":
            url = str(values["url"]).strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ComputerError("invalid_input", "url must start with http:// or https://.")
            fields["url"] = url
        if action == "wait":
            seconds = values.get("seconds")
            try:
                seconds = DEFAULT_WAIT_SECONDS if seconds in (None, "") else float(seconds)
            except (TypeError, ValueError):
                raise ComputerError("invalid_input", "seconds must be a number.") from None
            if not 0 <= seconds <= MAX_WAIT_SECONDS:
                raise ComputerError("invalid_input", f"seconds must be between 0 and {MAX_WAIT_SECONDS:g}.")
            fields["seconds"] = seconds
        return cls(**fields)

    def points(self) -> list[tuple[str, int, int]]:
        points = []
        if self.x is not None and self.y is not None:
            points.append(("x/y", self.x, self.y))
        if self.x2 is not None and self.y2 is not None:
            points.append(("x2/y2", self.x2, self.y2))
        return points

    def check_bounds(self, width: int, height: int) -> None:
        for label, x, y in self.points():
            if x >= width or y >= height:
                raise ComputerError(
                    "invalid_input",
                    f"{label} ({x}, {y}) is outside the last screenshot ({width}x{height}). "
                    "Use coordinates from the latest screenshot.",
                )

    def to_screen(self, scale: float) -> ComputerStep:
        """Map coordinates of a scaled screenshot back to screen pixels."""
        if scale == 1.0:
            return self
        mapped = {name: int(round(value * scale)) for name in ("x", "y", "x2", "y2")
                  if (value := getattr(self, name)) is not None}
        return replace(self, **mapped)

    def describe(self) -> dict[str, Any]:
        """Fields worth showing a person; never includes secret values."""
        if self.action == "type":
            if self.secret:
                return {"secret": self.secret}
            text = self.text or ""
            return {"text": text if len(text) <= 200 else text[:200] + "…", "characters": len(text)}
        if self.action == "key":
            return {"keys": "+".join(self.keys)}
        if self.action == "open_url":
            return {"url": self.url}
        if self.action == "wait":
            return {"seconds": self.seconds}
        details: dict[str, Any] = {name: getattr(self, name) for name in ("x", "y", "x2", "y2")
                                   if getattr(self, name) is not None}
        if self.action in CLICK_ACTIONS or self.action == "drag":
            details["button"] = self.button
        if self.action == "scroll":
            details.update(direction=self.direction, amount=self.amount)
        if self.modifiers:
            details["modifiers"] = "+".join(self.modifiers)
        return details


def _coordinate(name: str, value: Any) -> int:
    return _bounded_int(name, value, 0, MAX_COORDINATE)


def _bounded_int(name: str, value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ComputerError("invalid_input", f"{name} must be an integer.") from None
    if isinstance(value, float) and not value.is_integer():
        raise ComputerError("invalid_input", f"{name} must be an integer.")
    if not low <= number <= high:
        raise ComputerError("invalid_input", f"{name} must be between {low} and {high}.")
    return number


def _choice(name: str, value: Any, options: tuple[str, ...]) -> str:
    text = str(value)
    if text not in options:
        raise ComputerError("invalid_input", f"{name} must be one of: {', '.join(options)}.")
    return text


# Screenshots ---------------------------------------------------------------- #

# Larger images are downscaled before the model sees them; coordinates are mapped back.
MAX_IMAGE_EDGE = 1568
MAX_IMAGE_PIXELS = 1_150_000


@dataclass(frozen=True)
class ComputerScreenshot:
    """An image of the screen; ``width`` and ``height`` are in screen pixels."""

    data: bytes
    width: int
    height: int
    mime_type: str = "image/png"

    @classmethod
    def from_png(cls, data: bytes) -> ComputerScreenshot:
        width, height = png_size(data)
        return cls(data, width, height)


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ComputerError("upstream", "The desktop returned an image that is not a PNG.")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def fit_screenshot(shot: ComputerScreenshot) -> tuple[ComputerScreenshot, float]:
    """Downscale large screenshots; return the image and screen pixels per image pixel."""
    scale = max(1.0, shot.width / MAX_IMAGE_EDGE, shot.height / MAX_IMAGE_EDGE,
                math.sqrt(shot.width * shot.height / MAX_IMAGE_PIXELS))
    if scale == 1.0:
        return shot, 1.0
    from PIL import Image

    width, height = max(1, int(shot.width / scale)), max(1, int(shot.height / scale))
    with Image.open(io.BytesIO(shot.data)) as image:
        resized = image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    resized.save(output, format="PNG", optimize=True)
    return ComputerScreenshot(output.getvalue(), width, height), shot.width / width


@dataclass(frozen=True)
class ScreenInfo:
    """What the agent last saw: image size in its coordinates and the scale to the screen."""

    width: int
    height: int
    scale: float = 1.0


# Backend contract ----------------------------------------------------------- #


@dataclass(frozen=True)
class ViewerEndpoint:
    """A WebSocket that streams the desktop over RFB (VNC) for the dashboard viewer."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


class WorkspaceComputer(Protocol):
    """Raw desktop primitives of one workspace backend.

    ``screenshot`` and ``act`` start the desktop when needed. ``act`` receives
    validated steps in screen pixels and never ``wait``. Callers serialize
    calls per machine and enforce human control (see ``ComputerControl``).
    Failures raise ``ComputerError``.
    """

    async def status(self, locator: str) -> dict[str, Any]:
        ...

    async def start(self, locator: str, *, width: int, height: int) -> dict[str, Any]:
        ...

    async def screenshot(self, locator: str) -> ComputerScreenshot:
        ...

    async def act(self, locator: str, step: ComputerStep) -> dict[str, Any]:
        ...

    async def set_human_control(self, locator: str, active: bool) -> dict[str, Any]:
        """Mirror the dashboard's control lock upstream when the backend has one."""
        ...

    async def viewer_endpoint(self, locator: str) -> ViewerEndpoint:
        ...

    def invalidate(self, locator: str) -> None:
        """Forget cached state after the machine stopped, was restored or deleted."""
        ...


# X11 helpers ---------------------------------------------------------------- #

XDOTOOL_BUTTONS = {"left": 1, "middle": 2, "right": 3}
XDOTOOL_SCROLL = {"up": 4, "down": 5, "left": 6, "right": 7}
XDOTOOL_KEYS = {
    "ctrl": "ctrl", "alt": "alt", "shift": "shift", "super": "super",
    "enter": "Return", "escape": "Escape", "tab": "Tab", "backspace": "BackSpace", "delete": "Delete",
    "insert": "Insert", "space": "space", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down", "capslock": "Caps_Lock",
    "printscreen": "Print", "menu": "Menu", **{f"f{index}": f"F{index}" for index in range(1, 25)},
}
XDOTOOL_SYMBOLS = {
    " ": "space", "+": "plus", "-": "minus", "=": "equal", ",": "comma", ".": "period", "/": "slash",
    ";": "semicolon", "'": "apostrophe", "[": "bracketleft", "]": "bracketright", "\\": "backslash",
    "`": "grave", "!": "exclam", "@": "at", "#": "numbersign", "$": "dollar", "%": "percent",
    "^": "asciicircum", "&": "ampersand", "*": "asterisk", "(": "parenleft", ")": "parenright",
    "_": "underscore", "{": "braceleft", "}": "braceright", "|": "bar", ":": "colon", '"': "quotedbl",
    "<": "less", ">": "greater", "?": "question", "~": "asciitilde",
}
XDOTOOL_TYPE_DELAY_MS = 12
XDOTOOL_DRAG_STEPS = 12
XDOTOOL_TEXT_ENV = "K41_DESKTOP_TEXT"


@dataclass(frozen=True)
class ShellInput:
    command: str
    env: dict[str, str] | None = None
    timeout: int = 30


def xdotool_input(step: ComputerStep) -> ShellInput:
    """Translate a step into an ``xdotool`` command for X11 desktops.

    ``type`` steps must already carry their text (secrets resolved).
    """

    def keysym(key: str) -> str:
        if key in XDOTOOL_KEYS:
            return XDOTOOL_KEYS[key]
        return XDOTOOL_SYMBOLS.get(key, key)

    def held(command: str) -> str:
        if not step.modifiers:
            return command
        names = " ".join(keysym(key) for key in step.modifiers)
        # Release modifiers even when the command fails, so no key stays pressed.
        return f"xdotool keydown {names}; {command}; status=$?; xdotool keyup {names}; exit $status"

    action = step.action
    if action == "move":
        return ShellInput(held(f"xdotool mousemove {step.x} {step.y}"))
    if action in CLICK_ACTIONS:
        repeat = {"click": "", "double_click": "--repeat 2 ", "triple_click": "--repeat 3 "}[action]
        return ShellInput(held(f"xdotool mousemove {step.x} {step.y} click {repeat}{XDOTOOL_BUTTONS[step.button]}"))
    if action == "drag":
        button = XDOTOOL_BUTTONS[step.button]
        # Intermediate moves let applications register the drag gesture.
        path = " ".join(
            f"mousemove {round(step.x + (step.x2 - step.x) * index / XDOTOOL_DRAG_STEPS)} "
            f"{round(step.y + (step.y2 - step.y) * index / XDOTOOL_DRAG_STEPS)} sleep 0.02"
            for index in range(1, XDOTOOL_DRAG_STEPS + 1)
        )
        return ShellInput(held(f"xdotool mousemove {step.x} {step.y} mousedown {button} sleep 0.05 {path} mouseup {button}"))
    if action == "scroll":
        prefix = f"xdotool mousemove {step.x} {step.y}; " if step.x is not None else ""
        return ShellInput(held(f"{prefix}xdotool click --repeat {step.amount} {XDOTOOL_SCROLL[step.direction]}"))
    if action == "type":
        if step.text is None:
            raise ComputerError("invalid_input", "Resolve the secret before typing it.")
        seconds = len(step.text) * XDOTOOL_TYPE_DELAY_MS / 1000
        return ShellInput(f'xdotool type --delay {XDOTOOL_TYPE_DELAY_MS} -- "${XDOTOOL_TEXT_ENV}"',
                          {XDOTOOL_TEXT_ENV: step.text}, timeout=int(30 + seconds * 1.5))
    if action == "key":
        return ShellInput(f"xdotool key -- {shlex.quote('+'.join(keysym(key) for key in step.keys))}")
    if action == "open_url":
        return ShellInput(f"(nohup xdg-open {shlex.quote(step.url or '')} >/dev/null 2>&1 &)")
    raise ComputerError("invalid_input", f"Action {action} is not sent to the desktop.")


# Human control, serialization and viewers ---------------------------------- #

CONTROL_TTL_SECONDS = 30.0
TICKET_TTL_SECONDS = 60.0


@dataclass
class ComputerController:
    owner_id: str
    expires_at: float


@dataclass(frozen=True)
class ViewerTicket:
    backend: str
    locator: str
    owner_id: str
    expires_at: float


class ComputerControl:
    """State shared by agent tools and the dashboard for every workspace desktop.

    A person who takes control blocks agent input until they hand it back or
    stop renewing it. Agent calls on one machine run one at a time.
    """

    def __init__(self, *, clock: Any = None) -> None:
        self._clock = clock or time.monotonic
        self._controllers: dict[tuple[str, str], ComputerController] = {}
        self._tickets: dict[str, ViewerTicket] = {}
        self._closers: dict[tuple[str, str], set[asyncio.Event]] = {}
        self._screens: dict[tuple[str, str], ScreenInfo] = {}
        self._locks: dict[tuple[str, str, int], asyncio.Lock] = {}

    def lock(self, backend: str, locator: str) -> asyncio.Lock:
        key = (backend, locator, id(asyncio.get_running_loop()))
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    # Human control ------------------------------------------------------- #

    def controller(self, backend: str, locator: str) -> ComputerController | None:
        key = (backend, locator)
        current = self._controllers.get(key)
        if current is not None and current.expires_at <= self._clock():
            self._controllers.pop(key, None)
            return None
        return current

    def human_in_control(self, backend: str, locator: str) -> bool:
        return self.controller(backend, locator) is not None

    def assert_agent_may_act(self, backend: str, locator: str) -> None:
        if self.human_in_control(backend, locator):
            raise ComputerError("human_in_control",
                                "A person is controlling this desktop. Wait until they hand control back.")

    def take(self, backend: str, locator: str, owner_id: str) -> None:
        current = self.controller(backend, locator)
        if current is not None and current.owner_id != owner_id:
            raise ComputerError("controlled_by_other", "Another user is controlling this desktop.")
        self._controllers[(backend, locator)] = ComputerController(owner_id, self._clock() + CONTROL_TTL_SECONDS)

    def renew(self, backend: str, locator: str, owner_id: str) -> None:
        current = self.controller(backend, locator)
        if current is None or current.owner_id != owner_id:
            raise ComputerError("control_lost", "You no longer control this desktop.")
        current.expires_at = self._clock() + CONTROL_TTL_SECONDS

    def release(self, backend: str, locator: str, owner_id: str) -> bool:
        current = self.controller(backend, locator)
        if current is not None and current.owner_id == owner_id:
            self._controllers.pop((backend, locator), None)
            return True
        return False

    def state(self, backend: str, locator: str) -> dict[str, Any]:
        controller = self.controller(backend, locator)
        return {
            "app_controller": controller.owner_id if controller else None,
            "app_controller_expires_in": max(0.0, controller.expires_at - self._clock()) if controller else 0.0,
        }

    # Screen -------------------------------------------------------------- #

    def remember_screen(self, backend: str, locator: str, screen: ScreenInfo) -> None:
        self._screens[(backend, locator)] = screen

    def screen(self, backend: str, locator: str) -> ScreenInfo | None:
        return self._screens.get((backend, locator))

    # Viewers ------------------------------------------------------------- #

    def issue_ticket(self, backend: str, locator: str, owner_id: str) -> str:
        now = self._clock()
        self._tickets = {key: value for key, value in self._tickets.items() if value.expires_at > now}
        token = secrets.token_urlsafe(32)
        self._tickets[token] = ViewerTicket(backend, locator, owner_id, now + TICKET_TTL_SECONDS)
        return token

    def consume_ticket(self, token: str) -> ViewerTicket:
        ticket = self._tickets.pop(str(token or ""), None)
        if ticket is None or ticket.expires_at < self._clock():
            raise PermissionError("Viewer ticket is invalid or expired.")
        return ticket

    def register_viewer(self, backend: str, locator: str) -> asyncio.Event:
        closer = asyncio.Event()
        self._closers.setdefault((backend, locator), set()).add(closer)
        return closer

    def unregister_viewer(self, backend: str, locator: str, closer: asyncio.Event) -> None:
        closers = self._closers.get((backend, locator))
        if closers is not None:
            closers.discard(closer)
            if not closers:
                self._closers.pop((backend, locator), None)

    # Lifecycle ----------------------------------------------------------- #

    def invalidate(self, backend: str, locator: str) -> None:
        """Drop control and screen state and close viewers of a machine."""
        key = (backend, locator)
        self._controllers.pop(key, None)
        self._screens.pop(key, None)
        for closer in list(self._closers.get(key, ())):
            closer.set()

    def close(self) -> None:
        for backend, locator in list(self._closers):
            self.invalidate(backend, locator)
        self._tickets.clear()


_fallback_control: ComputerControl | None = None


def get_computer_control(container: Any = None) -> ComputerControl:
    """Return the container-scoped ``ComputerControl``."""
    global _fallback_control
    from agent.bootstrap.container import get_active_container

    active = container or get_active_container()
    if active is None:
        if _fallback_control is None:
            _fallback_control = ComputerControl()
        return _fallback_control
    with active._lazy_lock:
        control = getattr(active, "_computer_control", None)
        if control is None:
            control = active._computer_control = ComputerControl()
        return control


__all__ = [
    "CLICK_ACTIONS",
    "COMPUTER_ACTIONS",
    "COMPUTER_ACTION_FIELDS",
    "CONTROL_TTL_SECONDS",
    "ComputerControl",
    "ComputerError",
    "ComputerScreenshot",
    "ComputerStep",
    "INTENT_FIELDS",
    "KEY_NAMES_HINT",
    "LOCAL_ACTIONS",
    "MAX_TYPE_CHARS",
    "MAX_WAIT_SECONDS",
    "MODIFIER_KEYS",
    "MOUSE_BUTTONS",
    "POINTER_ACTIONS",
    "SCROLL_DIRECTIONS",
    "ScreenInfo",
    "ShellInput",
    "TICKET_TTL_SECONDS",
    "ViewerEndpoint",
    "ViewerTicket",
    "WorkspaceComputer",
    "fit_screenshot",
    "get_computer_control",
    "normalize_key",
    "normalize_keys",
    "png_size",
    "xdotool_input",
]
