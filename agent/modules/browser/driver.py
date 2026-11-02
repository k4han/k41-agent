"""Stagehand lifecycle and a private, allow-listed CDP viewer transport."""

import asyncio
import base64
import contextlib
import io
import json
import math
import socket
import sys
import time
from concurrent.futures import Future
from pathlib import Path
from threading import Thread

import httpx
from websockets.asyncio.client import connect

from agent.shared.infrastructure.browser import find_browser


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StagehandDriver:
    def __init__(self, browser, stagehand, facade, port):
        self.browser = browser
        self.stagehand = stagehand
        self.facade = facade
        self.port = port

    @classmethod
    async def launch(cls, profile: Path, artifacts: Path, executable_path: str = ""):
        if sys.platform == "win32":
            return await ThreadedDriver.launch(profile, artifacts, executable_path)
        return await cls._launch(profile, artifacts, executable_path)

    @classmethod
    async def _launch(cls, profile: Path, artifacts: Path, executable_path: str = ""):
        from stagehand import Stagehand, local_browser

        port = available_port()
        browser = await local_browser.launch(
            headless=True, port=port, user_data_dir=profile, preserve_user_data_dir=True,
            executable_path=executable_path or find_browser(), chromium_sandbox=True,
            args=["--remote-debugging-address=127.0.0.1"],
            viewport_width=1280, viewport_height=720, accept_downloads=False,
        )
        try:
            stagehand = await Stagehand.create(browser=browser)
            # Chrome window dimensions include browser chrome on some platforms.
            for page in await browser.context.pages():
                await page.set_viewport_size(1280, 720)
            driver = cls(browser, stagehand, None, port)
            await driver.reset_facade(artifacts)
            return driver
        except BaseException:
            await browser.close()
            raise

    async def close(self):
        # Browser.close flushes origin storage and also interrupts browser-side
        # JavaScript. Do this before waiting for any extension RPC cleanup.
        try:
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                    version = (await client.get(f"http://127.0.0.1:{self.port}/json/version")).json()
                async with connect(version["webSocketDebuggerUrl"]) as connection:
                    await connection.send(json.dumps({"id": 1, "method": "Browser.close"}))
                    await asyncio.wait_for(connection.wait_closed(), 5)
        finally:
            try:
                await self.browser.close()
            finally:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.stagehand.close(), 5)

    async def tabs(self) -> list[dict]:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.get(f"http://127.0.0.1:{self.port}/json/list")
            response.raise_for_status()
        return [{"id": item["id"], "title": item.get("title", ""), "url": item.get("url", "")}
                for item in response.json() if item.get("type") == "page"
                and not item.get("url", "").startswith(("chrome-extension:", "devtools:"))]

    async def target_url(self, target_id: str) -> str:
        # Only targets from this browser may be attached. CDP addresses stay server-side.
        if target_id not in {tab["id"] for tab in await self.tabs()}:
            raise ValueError("Browser tab no longer exists.")
        return f"ws://127.0.0.1:{self.port}/devtools/page/{target_id}"

    async def active_tab_id(self):
        page = await self.browser.context.active_page()
        return page.page_id if page else None

    async def watch_tabs(self, notify):
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.get(f"http://127.0.0.1:{self.port}/json/version")
            response.raise_for_status()
        async with connect(response.json()["webSocketDebuggerUrl"]) as connection:
            await connection.send(json.dumps({"id": 1, "method": "Target.setDiscoverTargets",
                                              "params": {"discover": True}}))
            async for raw in connection:
                message = json.loads(raw)
                if message.get("method") in {"Target.targetCreated", "Target.targetInfoChanged", "Target.targetDestroyed"}:
                    notify()
        raise ConnectionError("Browser target observer disconnected.")

    async def reset_facade(self, artifacts: Path, active_tab_id: str | None = None):
        from stagehand_middleware.facade import StagehandFacadeTools

        if active_tab_id:
            for page in await self.browser.context.pages():
                if page.page_id == active_tab_id:
                    await self.browser.context.set_active_page(page)
                    break
        async def reopen():
            await self.reset_facade(artifacts)

        self.facade = StagehandFacadeTools(self.stagehand, artifact_root=artifacts,
                                           on_close_requested=reopen)


class ThreadedFacade:
    """Keep SDK objects, locks, sockets and subprocesses on their owning loop."""

    def __init__(self, driver):
        self.driver = driver

    async def run(self, **values):
        return await self.driver.submit(self.driver.inner.facade.run(**values))

    async def snapshot(self, **values):
        return await self.driver.submit(self.driver.inner.facade.snapshot(**values))

    async def screenshot(self, **values):
        return await self.driver.submit(self.driver.inner.facade.screenshot(**values))


class ThreadedDriver:
    """Windows K41 uses a selector loop; Stagehand requires proactor subprocesses."""

    @classmethod
    async def launch(cls, profile, artifacts, executable_path):
        ready = Future()

        def worker():
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            ready.set_result(loop)
            try:
                loop.run_forever()
            finally:
                tasks = asyncio.all_tasks(loop)
                for task in tasks:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()

        driver = cls()
        driver.thread = Thread(target=worker, name="k41-browser", daemon=True)
        driver.thread.start()
        try:
            driver.loop = await asyncio.shield(asyncio.wrap_future(ready))
            driver.inner = await driver.submit(StagehandDriver._launch(profile, artifacts, executable_path))
            driver.facade = ThreadedFacade(driver)
            return driver
        except BaseException:
            if not hasattr(driver, "loop"):
                driver.loop = await asyncio.shield(asyncio.wrap_future(ready))
            await driver.stop()
            raise

    @property
    def port(self):
        return self.inner.port

    async def submit(self, operation):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(operation, self.loop))

    async def tabs(self):
        return await self.submit(self.inner.tabs())

    async def target_url(self, target_id):
        return await self.submit(self.inner.target_url(target_id))

    async def active_tab_id(self):
        return await self.submit(self.inner.active_tab_id())

    async def watch_tabs(self, notify):
        await StagehandDriver.watch_tabs(self, notify)

    async def reset_facade(self, artifacts, active_tab_id=None):
        await self.submit(self.inner.reset_facade(artifacts, active_tab_id))

    async def stop(self):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
        await asyncio.to_thread(self.thread.join, 15)
        if self.thread.is_alive():
            raise RuntimeError("Browser worker did not finish shutting down.")

    async def close(self):
        if self.loop.is_closed():
            return
        try:
            await self.submit(self.inner.close())
        finally:
            await self.stop()


class CDPViewer:
    """One viewer connection; input contains no arbitrary CDP method names."""

    IDLE_SNAPSHOT_DELAY = 0.1

    def __init__(self, driver, target_id: str, *, visible=True, width=1280, height=None,
                 quality=50, every_nth_frame=3):
        self.driver = driver
        self.target_id = target_id
        self.socket = None
        self.reader = None
        self.snapshotter = None
        self.closed = asyncio.Event()
        self.next_id = 0
        self.pending = {}
        self.frames = asyncio.Queue(maxsize=1)
        self.frame_received = asyncio.Event()
        self.latest_frame = None
        self.previous_data = None
        self.visible = visible
        self.screencast_active = False
        self.capture_revision = 0
        self.source_sequence = 0
        self.last_snapshot_sequence = -1
        self.last_snapshot_revision = -1
        self.source_frames = 0
        self.source_frames_dropped = 0
        self.snapshots = 0
        self.snapshots_dropped = 0
        self.capture_ms = 0
        self.resize_ms = 0
        self.stream_options = self._options(width, height, quality, every_nth_frame)
        self.configure_lock = asyncio.Lock()
        self.capture_lock = asyncio.Lock()

    @staticmethod
    def _options(width, height, quality, every_nth_frame):
        width = max(1, min(8192, int(width)))
        height = max(1, min(8192, int(height if height is not None else width * 720 / 1280)))
        return {"format": "jpeg", "quality": max(1, min(100, int(quality))),
                "maxWidth": width, "maxHeight": height,
                "everyNthFrame": max(1, min(4, int(every_nth_frame)))}

    def metrics(self):
        return {"source_frames": self.source_frames, "source_frames_dropped": self.source_frames_dropped,
                "snapshots": self.snapshots, "snapshots_dropped": self.snapshots_dropped,
                "capture_ms": round(self.capture_ms, 1), "resize_ms": round(self.resize_ms, 1)}

    async def __aenter__(self):
        self.socket = await connect(await self.driver.target_url(self.target_id), max_size=16 * 1024 * 1024)
        self.reader = asyncio.create_task(self._read())
        self.snapshotter = asyncio.create_task(self._capture_idle_frame())
        try:
            await self.call("Page.enable")
            if self.visible:
                # Capture may start even if its command response is interrupted.
                self.screencast_active = True
                await self.call("Page.startScreencast", self.stream_options)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *args):
        if self.snapshotter:
            self.snapshotter.cancel()
        try:
            if self.socket:
                if self.screencast_active and not self.closed.is_set():
                    with contextlib.suppress(Exception):
                        await self.call("Page.stopScreencast")
                        self.screencast_active = False
                await self.socket.close()
        finally:
            if self.reader:
                self.reader.cancel()
            await asyncio.gather(*(task for task in (self.reader, self.snapshotter) if task), return_exceptions=True)

    async def wait_closed(self):
        await self.closed.wait()

    async def call(self, method: str, params=None):
        if self.closed.is_set():
            raise ConnectionError("Browser connection closed.")
        self.next_id += 1
        call_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[call_id] = future
        try:
            await self.socket.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
            return await asyncio.wait_for(future, 5)
        finally:
            self.pending.pop(call_id, None)

    async def _read(self):
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError("Browser command failed."))
                        else:
                            future.set_result(message.get("result", {}))
                elif message.get("method") == "Page.screencastFrame":
                    frame = message["params"]
                    self.latest_frame = frame
                    self.source_frames += 1
                    if self.visible:
                        if frame["data"] != self.previous_data:
                            self.source_sequence += 1
                            self.frame_received.set()
                        self._publish_frame(frame)
                        self.previous_data = frame["data"]
                    # The bounded latest-frame slot owns source backpressure. Network
                    # credits are managed separately so capture does not wait a full RTT.
                    self.next_id += 1
                    await self.socket.send(json.dumps({"id": self.next_id, "method": "Page.screencastFrameAck",
                                                       "params": {"sessionId": frame["sessionId"]}}))
        finally:
            self.closed.set()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Browser connection closed."))

    def _publish_frame(self, frame):
        # A completed snapshot supersedes unchanged source feedback, including
        # while that snapshot is already waiting for network delivery.
        if (not frame.get("priority") and self.source_sequence == self.last_snapshot_sequence
                and self.capture_revision == self.last_snapshot_revision):
            self.source_frames_dropped += 1
            return
        if self.frames.full():
            previous = self.frames.get_nowait()
            if previous.get("priority"):
                self.snapshots_dropped += 1
            else:
                self.source_frames_dropped += 1
        self.frames.put_nowait({**frame, "capture_revision": self.capture_revision,
                               "captured_at": frame.get("captured_at", time.monotonic())})

    async def configure(self, *, visible=True, width=1280, height=None, quality=50, every_nth_frame=3, refresh=False):
        options = self._options(width, height, quality, every_nth_frame)
        async with self.configure_lock:
            if visible == self.visible and options == self.stream_options:
                if visible and refresh:
                    self.capture_revision += 1
                    await self.capture_frame()
                return
            self.capture_revision += 1
            self.visible = False
            self.frame_received.clear()
            if self.screencast_active:
                await self.call("Page.stopScreencast")
                self.screencast_active = False
            while not self.frames.empty():
                self.frames.get_nowait()
            self.stream_options = options
            self.visible = visible
            if visible:
                self.previous_data = None
                self.screencast_active = True
                await self.call("Page.startScreencast", options)
                await self.capture_frame()

    async def capture_frame(self, *, sequence=None):
        async with self.capture_lock:
            if not self.visible:
                return
            if sequence is not None and (sequence != self.source_sequence or sequence <= self.last_snapshot_sequence):
                return
            await self._capture_frame()

    async def _capture_frame(self):
        revision = self.capture_revision
        sequence = self.source_sequence
        options = self.stream_options
        started = time.monotonic()
        metadata = dict((self.latest_frame or {}).get("metadata", {}))
        layout = await self.call("Page.getLayoutMetrics")
        viewport = layout.get("cssVisualViewport") or layout.get("cssLayoutViewport") or {}
        x, y = viewport.get("pageX", metadata.get("scrollOffsetX", 0)), viewport.get("pageY", metadata.get("scrollOffsetY", 0))
        snapshot = await self.call("Page.captureScreenshot", {
            "format": "jpeg", "quality": options["quality"], "captureBeyondViewport": False,
        })
        captured = time.monotonic()
        self.snapshots += 1
        self.capture_ms = (captured - started) * 1000
        if not self.visible or revision != self.capture_revision or sequence != self.source_sequence:
            self.snapshots_dropped += 1
            return
        # CDP screenshot clips can resize the viewport and trigger another paint.
        # Resize the captured pixels off the event loop instead.
        data, width, height = await asyncio.to_thread(self._resize_capture, snapshot["data"], options)
        self.resize_ms = (time.monotonic() - captured) * 1000
        metadata.update(deviceWidth=width, deviceHeight=height, scrollOffsetX=x, scrollOffsetY=y)
        if self.visible and revision == self.capture_revision and sequence == self.source_sequence:
            self.last_snapshot_sequence = sequence
            self.last_snapshot_revision = revision
            self._publish_frame({"metadata": metadata, "data": data, "captured_at": started, "priority": True})
        else:
            self.snapshots_dropped += 1

    @staticmethod
    def _resize_capture(data: str, options) -> tuple[bytes, int, int]:
        """Return JPEG bytes and the original viewport dimensions."""
        from PIL import Image

        raw = base64.b64decode(data, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width <= options["maxWidth"] and height <= options["maxHeight"]:
                return raw, width, height
            image.thumbnail((options["maxWidth"], options["maxHeight"]), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=options["quality"])
            return output.getvalue(), width, height

    async def _capture_idle_frame(self):
        try:
            while True:
                await self.frame_received.wait()
                while True:
                    self.frame_received.clear()
                    try:
                        await asyncio.wait_for(self.frame_received.wait(), self.IDLE_SNAPSHOT_DELAY)
                    except TimeoutError:
                        break
                if self.visible and self.latest_frame:
                    await self.capture_frame(sequence=self.source_sequence)
        finally:
            self.closed.set()

    async def activate(self):
        await self.call("Page.bringToFront")

    @staticmethod
    def number(value, minimum, maximum):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Invalid input coordinate.")
        return max(minimum, min(maximum, value))

    async def input(self, event: dict):
        kind = event.get("type")
        modifiers = int(self.number(event.get("modifiers", 0), 0, 15))
        if kind in {"mousePressed", "mouseReleased", "mouseMoved", "mouseWheel"}:
            button = event.get("button", "none")
            if button not in {"none", "left", "right", "middle"}:
                raise ValueError("Invalid mouse button.")
            params = {"type": kind, "x": self.number(event.get("x"), 0, 1280),
                      "y": self.number(event.get("y"), 0, 720), "button": button,
                      "modifiers": modifiers, "buttons": int(self.number(event.get("buttons", 0), 0, 7))}
            if kind == "mouseWheel":
                params.update(deltaX=self.number(event.get("deltaX", 0), -2000, 2000),
                              deltaY=self.number(event.get("deltaY", 0), -2000, 2000))
            elif kind in {"mousePressed", "mouseReleased"}:
                params["clickCount"] = int(self.number(event.get("clickCount", 1), 1, 3))
            await self.call("Input.dispatchMouseEvent", params)
        elif kind == "text":
            text = event.get("text")
            if not isinstance(text, str) or len(text) > 10000:
                raise ValueError("Input text exceeds the viewer limit.")
            await self.call("Input.insertText", {"text": text})
        elif kind in {"keyDown", "keyUp"}:
            key, code = event.get("key", ""), event.get("code", "")
            if not isinstance(key, str) or not isinstance(code, str) or len(key) > 40 or len(code) > 40:
                raise ValueError("Invalid key.")
            await self.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": code,
                "modifiers": modifiers,
                **({"text": "\r", "unmodifiedText": "\r"} if key == "Enter" and kind == "keyDown" else {}),
                "windowsVirtualKeyCode": int(self.number(event.get("keyCode", 0), 0, 255))})
        elif kind == "navigate":
            url = event.get("url", "")
            if not isinstance(url, str) or len(url) > 8192 or not url.startswith(("https://", "http://")):
                raise ValueError("Navigation requires an HTTP or HTTPS URL.")
            await self.call("Page.navigate", {"url": url})
        elif kind in {"back", "forward"}:
            history = await self.call("Page.getNavigationHistory")
            index = history["currentIndex"] + (-1 if kind == "back" else 1)
            if 0 <= index < len(history["entries"]):
                await self.call("Page.navigateToHistoryEntry", {"entryId": history["entries"][index]["id"]})
        elif kind == "reload":
            await self.call("Page.reload")
        else:
            raise ValueError("Unsupported viewer input.")
