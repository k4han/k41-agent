"""Bounded browser streaming, independent control traffic, and viewer telemetry."""

import asyncio
import base64
import json
import math
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import count


FRAME_WINDOW = 3
FRAME_BYTE_WINDOW = 16 * 1024 * 1024
SEND_TIMEOUT = 5


@dataclass
class BrowserStreamState:
    visible: bool = True
    max_width: int = 1280
    max_height: int = 720
    frame_ids: object = field(default_factory=count)
    pending: dict[int, float] = field(default_factory=dict)
    pending_sizes: dict[int, int] = field(default_factory=dict)
    pending_captured: dict[int, float] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    settings_changed: asyncio.Event = field(default_factory=asyncio.Event)
    credits: asyncio.Event = field(default_factory=asyncio.Event)
    streaming: asyncio.Event = field(default_factory=asyncio.Event)
    status_ready: asyncio.Event = field(default_factory=asyncio.Event)
    sequence: int = 0
    visibility_revision: int = 0
    ack_ms: float = 0
    render_ms: float = 0
    decode_ms: float = 0
    client_queue_ms: float = 0
    send_ms: float = 0
    queue_ms: float = 0
    capture_ack_ms: float = 0
    frame_bytes: int = 0
    pacing_dropped: int = 0
    frames_sent: int = 0
    bytes_sent: int = 0
    frames_dropped: int = 0
    quality: int = 65
    every_nth_frame: int = 1
    last_adaptation: float = 0

    @property
    def pending_bytes(self):
        return sum(self.pending_sizes.values())

    @property
    def target_fps(self):
        return {1: 30, 2: 20, 3: 10}.get(self.every_nth_frame, 10)

    def clear_pending(self):
        self.pending.clear()
        self.pending_sizes.clear()
        self.pending_captured.clear()

    def refresh_settings(self, config):
        if config is None:
            return
        for name, default, maximum in (("quality", 65, 100), ("max_width", 1280, 8192), ("max_height", 720, 8192)):
            value = config.get(f"browser.stream_{name}", default)
            value = value if type(value) is int and 1 <= value <= maximum else default
            if getattr(self, name) != value:
                setattr(self, name, value)
                self.settings_changed.set()

    def acknowledge(self, event):
        frame_id = event.get("frame_id")
        if type(frame_id) is not int or frame_id not in self.pending:
            return
        now = time.monotonic()
        elapsed = (now - self.pending.pop(frame_id)) * 1000
        self.pending_sizes.pop(frame_id, None)
        captured = self.pending_captured.pop(frame_id, None)
        if captured is not None:
            self.capture_ack_ms = (now - captured) * 1000
        self.ack_ms = elapsed if not self.ack_ms else self.ack_ms * 0.8 + elapsed * 0.2
        for name, key in (("render_ms", "render_ms"), ("decode_ms", "decode_ms"), ("client_queue_ms", "queue_ms")):
            value = event.get(key, 0)
            if event.get("drawn") is not False and type(value) in {int, float} and math.isfinite(value):
                setattr(self, name, max(0, min(10000, value)))
        self.frames_dropped += event.get("drawn") is False
        self.credits.set()
        if time.monotonic() - self.last_adaptation < 2:
            return
        self.last_adaptation = time.monotonic()
        # Intentional pacing must not prevent recovery to a higher frame rate.
        queue_pressure = max(0, self.queue_ms - 1000 / self.target_fps)
        load = max(self.ack_ms, self.render_ms * 4, self.decode_ms * 4,
                   self.send_ms * 2, queue_pressure * 2, self.client_queue_ms * 2)
        if self.pending_bytes >= FRAME_BYTE_WINDOW * 0.75:
            load = max(load, 251)
        interval = 3 if load > 250 else 2 if load > 120 else 1
        # Avoid restarting capture repeatedly near a frame-rate boundary.
        if interval < self.every_nth_frame and load > (180 if self.every_nth_frame == 3 else 80):
            return
        if interval != self.every_nth_frame:
            self.every_nth_frame = interval
            self.settings_changed.set()

    def metrics(self):
        return {"ack_ms": round(self.ack_ms, 1), "render_ms": round(self.render_ms, 1),
                "frames_sent": self.frames_sent, "bytes_sent": self.bytes_sent,
                "frames_dropped": self.frames_dropped, "quality": self.quality, "frame_window": FRAME_WINDOW,
                "pending_bytes": self.pending_bytes, "frame_byte_window": FRAME_BYTE_WINDOW,
                "frame_bytes": self.frame_bytes, "target_fps": self.target_fps,
                "send_ms": round(self.send_ms, 1), "queue_ms": round(self.queue_ms, 1),
                "decode_ms": round(self.decode_ms, 1), "client_queue_ms": round(self.client_queue_ms, 1),
                "capture_ack_ms": round(self.capture_ack_ms, 1), "pacing_dropped": self.pacing_dropped}


class BrowserOutputQueue:
    """One writer, bounded controls, and at most one waiting image."""

    def __init__(self):
        self.controls = deque()
        self.frame = None
        self.ready = asyncio.Event()

    def put_json(self, payload, receipt=None):
        if len(self.controls) >= 64:
            raise ConnectionError("Browser output queue is full.")
        self.controls.append((payload, receipt))
        self.ready.set()

    async def send_json(self, payload):
        receipt = asyncio.get_running_loop().create_future()
        self.put_json(payload, receipt)
        await receipt

    async def send_frame(self, frame):
        if self.frame is not None:
            raise ConnectionError("Browser image queue is full.")
        receipt = asyncio.get_running_loop().create_future()
        self.frame = frame, receipt
        self.ready.set()
        await receipt

    def take(self):
        if self.controls:
            item = self.controls.popleft()
            if not self.controls and self.frame is None:
                self.ready.clear()
            return False, item
        item, self.frame = self.frame, None
        self.ready.clear()
        return True, item


class BrowserInputQueue:
    """Coalesce adjacent motion without crossing a click, key, or tab boundary."""

    def __init__(self):
        self.items = deque()
        self.ready = asyncio.Event()

    def clear(self):
        self.items.clear()
        self.ready.clear()

    def put(self, event, handoff_id, revision):
        kind = event.get("type")
        if self.items and kind in {"mouseMoved", "mouseWheel"}:
            previous, previous_handoff, previous_revision = self.items[-1]
            if (previous.get("type"), previous_handoff, previous_revision,
                previous.get("modifiers"), previous.get("buttons")) == (
                    kind, handoff_id, revision, event.get("modifiers"), event.get("buttons")):
                if kind == "mouseWheel":
                    event = {**event, "deltaX": self._delta(previous, event, "deltaX"),
                             "deltaY": self._delta(previous, event, "deltaY")}
                self.items[-1] = event, handoff_id, revision
                return
        if len(self.items) >= 64:
            raise ValueError("Browser input queue is full. Reconnect before continuing.")
        self.items.append((event, handoff_id, revision))
        self.ready.set()

    @staticmethod
    def _delta(previous, event, key):
        values = [previous.get(key, 0), event.get(key, 0)]
        if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
            raise ValueError("Invalid wheel delta.")
        return max(-2000, min(2000, sum(values)))

    async def get(self):
        await self.ready.wait()
        item = self.items.popleft()
        if not self.items:
            self.ready.clear()
        return item


async def stream_tab(websocket, browser, current, target_id, viewer_id, viewer_factory, target_selector,
                     follow_agent=True, state=None):
    state = state or BrowserStreamState()
    config = getattr(getattr(browser, "container", None), "config_service", None)
    state.refresh_settings(config)
    state.clear_pending()
    state.streaming.clear()
    state.status_ready.clear()
    state.settings_changed.set()
    state.credits.set()
    state.changed.set()
    inputs = BrowserInputQueue()
    output = BrowserOutputQueue()
    subscribers = getattr(current, "subscribers", set())

    def notify():
        if hasattr(current, "notify"):
            current.notify()
        state.changed.set()

    async with viewer_factory(current.driver, target_id, visible=state.visible, width=state.max_width,
                              height=state.max_height, quality=state.quality,
                              every_nth_frame=state.every_nth_frame) as viewer:
        subscribers.add(state.changed)

        def current_frame(frame):
            return frame.get("capture_revision") == getattr(viewer, "capture_revision", None)

        async def write():
            while True:
                await output.ready.wait()
                binary, (item, receipt) = output.take()
                if binary:
                    frame, payload, frame_id, revision = item
                    if (not state.visible or not state.streaming.is_set() or not state.status_ready.is_set()
                            or revision != state.visibility_revision or not current_frame(frame)):
                        if not receipt.done():
                            receipt.set_result(False)
                        continue
                    started = time.monotonic()
                    captured = frame.get("captured_at", started)
                    state.queue_ms = max(0, (started - captured) * 1000)
                    state.pending[frame_id] = started
                    state.pending_sizes[frame_id] = len(payload)
                    state.pending_captured[frame_id] = captured
                    await asyncio.wait_for(websocket.send_bytes(payload), SEND_TIMEOUT)
                    elapsed = (time.monotonic() - started) * 1000
                    state.send_ms = elapsed if not state.send_ms else state.send_ms * 0.8 + elapsed * 0.2
                    state.frame_bytes = len(payload)
                    state.frames_sent += 1
                    state.bytes_sent += len(payload)
                else:
                    await asyncio.wait_for(websocket.send_json(item), SEND_TIMEOUT)
                if receipt is not None and not receipt.done():
                    receipt.set_result(True)

        async def configure():
            configured_visibility = -1
            while True:
                await state.settings_changed.wait()
                state.settings_changed.clear()
                state.streaming.clear()
                revision = state.visibility_revision
                if hasattr(viewer, "configure"):
                    await viewer.configure(visible=state.visible,
                        width=state.max_width, height=state.max_height,
                        quality=state.quality, every_nth_frame=state.every_nth_frame,
                        refresh=revision != configured_visibility)
                configured_visibility = revision
                if state.visible and not state.settings_changed.is_set():
                    state.streaming.set()
                state.changed.set()

        async def frames():
            last_sent = 0

            async def wait_for_credit():
                state.credits.clear()
                try:
                    await asyncio.wait_for(state.credits.wait(), 1)
                except TimeoutError:
                    if state.pending and time.monotonic() - min(state.pending.values()) > 15:
                        raise ConnectionError("Browser rendering stopped acknowledging frames.")

            def latest(frame):
                while not viewer.frames.empty():
                    frame = viewer.frames.get_nowait()
                    state.pacing_dropped += 1
                return frame

            while not current.closed:
                await state.streaming.wait()
                await state.status_ready.wait()
                if len(state.pending) >= FRAME_WINDOW:
                    await wait_for_credit()
                    continue
                frame = await viewer.frames.get()
                # A read started before a pause can receive the sole fresh resume image.
                # Validate the source epoch after the gates, never before waiting for it.
                await state.streaming.wait()
                await state.status_ready.wait()
                frame = latest(frame)
                if not state.visible or not current_frame(frame):
                    continue
                revision = state.visibility_revision
                delay = last_sent + 1 / state.target_fps - time.monotonic()
                if delay > 0 and not frame.get("priority"):
                    await asyncio.sleep(delay)
                    frame = latest(frame)
                decoded_frame = None
                while True:
                    if (not state.visible or not state.streaming.is_set() or not current_frame(frame)
                            or revision != state.visibility_revision):
                        break
                    if frame is not decoded_frame:
                        jpeg = base64.b64decode(frame["data"], validate=True)
                        decoded_frame = frame
                    frame_bytes = 12 + len(jpeg)
                    if frame_bytes > FRAME_BYTE_WINDOW:
                        raise ValueError("Browser image exceeds the message limit.")
                    if state.pending_bytes + frame_bytes <= FRAME_BYTE_WINDOW:
                        break
                    await wait_for_credit()
                    frame = latest(frame)
                if (not state.visible or not state.streaming.is_set() or not current_frame(frame)
                        or revision != state.visibility_revision):
                    continue
                frame_id = next(state.frame_ids) % 0xFFFFFFFF + 1
                metadata = frame["metadata"]
                payload = struct.pack("!III", frame_id, int(metadata.get("deviceWidth", 1280)),
                                      int(metadata.get("deviceHeight", 800))) + jpeg
                await output.send_frame((frame, payload, frame_id, revision))
                last_sent = time.monotonic()
            raise ConnectionError("Browser session closed.")

        async def statuses():
            initial = True
            previous = None
            while not current.closed:
                state.changed.clear()
                data = await browser.viewer_status(current) if browser else {}
                if current.closed or (data and data.get("generation") != current.generation):
                    await output.send_json({"type": "session_closed"})
                    raise ConnectionError("Browser session closed.")
                tabs = data.get("tabs") if data else await current.driver.tabs()
                if not tabs:
                    raise ConnectionError("The browser has no visible tabs.")
                if target_id not in {tab["id"] for tab in tabs} or (follow_agent and not current.handoff_id):
                    next_target = await target_selector(current, tabs, target_id)
                    if next_target != target_id:
                        return next_target, follow_agent
                controller = ("agent" if not current.handoff_id else "available" if current.controller is None
                              else "you" if current.controller == viewer_id else "other")
                payload = {**data, "type": "status", "tabs": tabs, "tab_id": target_id,
                    "follow_agent": follow_agent, "mode": "human" if current.handoff_id else "agent",
                    "can_control": bool(current.handoff_id and current.controller in {None, viewer_id}),
                    "controller": controller, "paused": not state.visible or not state.streaming.is_set(),
                    "visibility_revision": state.visibility_revision}
                if initial or payload != previous:
                    state.sequence += 1
                    await output.send_json({**payload, "reset": initial, "sequence": state.sequence})
                    if (not payload["paused"] and state.visible and state.streaming.is_set()
                            and payload["visibility_revision"] == state.visibility_revision):
                        state.status_ready.set()
                    previous, initial = payload, False
                try:
                    await asyncio.wait_for(state.changed.wait(), 10)
                    # Coalesce target discovery bursts and service notifications.
                    await asyncio.sleep(0.025)
                except TimeoutError:
                    pass
            await output.send_json({"type": "session_closed"})
            raise ConnectionError("Browser session closed.")

        async def receive():
            while True:
                raw = await websocket.receive_text()
                if len(raw) > 65536:
                    raise ValueError("Viewer input exceeds the message limit.")
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError("Invalid viewer input.")
                kind = event.get("type")
                if kind == "frame_ack":
                    state.acknowledge(event)
                elif kind == "ping":
                    nonce = event.get("id")
                    if type(nonce) is not int or not 0 <= nonce <= 0xFFFFFFFF:
                        raise ValueError("Invalid heartbeat.")
                    state.refresh_settings(config)
                    metrics = {**state.metrics(), **(viewer.metrics() if hasattr(viewer, "metrics") else {})}
                    output.put_json({"type": "pong", "id": nonce, "metrics": metrics})
                elif kind in {"visibility", "resize"}:
                    # Older viewers may send dimensions; capture size comes only from settings.
                    state.refresh_settings(config)
                    if kind == "visibility":
                        visible = event.get("visible")
                        if type(visible) is not bool:
                            raise ValueError("Invalid viewer visibility.")
                        if visible != state.visible:
                            state.visible = visible
                            revision = event.get("revision")
                            state.visibility_revision = (revision if type(revision) is int
                                and state.visibility_revision < revision <= 0xFFFFFFFF else state.visibility_revision + 1)
                            state.clear_pending()
                            inputs.clear()
                            state.credits.set()
                            state.streaming.clear()
                            state.status_ready.clear()
                            state.settings_changed.set()
                            state.changed.set()
                else:
                    inputs.put(event, current.handoff_id, state.visibility_revision)

        async def execute_inputs():
            while True:
                event, handoff_id, revision = await inputs.get()
                kind = event.get("type")
                if kind in {"select_tab", "follow_agent"}:
                    tabs = await current.driver.tabs()
                    if not tabs:
                        raise ConnectionError("The browser has no visible tabs.")
                    if kind == "follow_agent":
                        return await target_selector(current, tabs, target_id), True
                    tab_id = event.get("tab_id")
                    if tab_id not in {tab["id"] for tab in tabs}:
                        output.put_json({"type": "error", "message": "Browser tab no longer exists."})
                        continue
                    async with current.lock:
                        if current.handoff_id and current.controller in {None, viewer_id}:
                            current.controller = viewer_id
                            current.active_tab_id = tab_id
                            current.last_activity = time.monotonic()
                            notify()
                    return tab_id, False
                async with current.lock:
                    if (current.closed or not current.handoff_id or current.handoff_id != handoff_id
                            or event.get("handoff_id", handoff_id) != handoff_id
                            or current.controller not in {None, viewer_id} or not state.visible
                            or revision != state.visibility_revision):
                        output.put_json({"type": "error", "message": "Browser input is available only during your handoff."})
                        continue
                    control_changed = current.controller != viewer_id or current.active_tab_id != target_id
                    current.controller = viewer_id
                    current.active_tab_id = target_id
                    if control_changed:
                        notify()
                    await viewer.activate()
                    await viewer.input(event)
                    current.last_activity = time.monotonic()
                input_id = event.get("input_id")
                if type(input_id) is int and 0 < input_id <= 0xFFFFFFFF:
                    output.put_json({"type": "input_ack", "input_id": input_id})

        tasks = [asyncio.create_task(operation()) for operation in
                 (frames, statuses, receive, execute_inputs, configure, write, viewer.wait_closed)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if tasks[-1] in done:
                raise ConnectionError("Browser stream connection closed.")
            return next(task.result() for task in done if task.result() is not None)
        finally:
            subscribers.discard(state.changed)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
