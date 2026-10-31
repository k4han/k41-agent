"""Stagehand lifecycle and a private, allow-listed CDP viewer transport."""

import asyncio
import contextlib
import json
import math
import socket
import sys
from concurrent.futures import Future
from pathlib import Path
from threading import Thread

import httpx
from websockets.asyncio.client import connect


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
            executable_path=executable_path or None, chromium_sandbox=True,
            args=["--remote-debugging-address=127.0.0.1"],
            viewport_width=1280, viewport_height=800, accept_downloads=False,
        )
        try:
            stagehand = await Stagehand.create(browser=browser)
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

    def __init__(self, driver, target_id: str):
        self.driver = driver
        self.target_id = target_id
        self.socket = None
        self.reader = None
        self.next_id = 0
        self.pending = {}
        self.frames = asyncio.Queue(maxsize=1)

    async def __aenter__(self):
        self.socket = await connect(await self.driver.target_url(self.target_id), max_size=16 * 1024 * 1024)
        self.reader = asyncio.create_task(self._read())
        try:
            await self.call("Page.enable")
            await self.call("Page.startScreencast", {"format": "jpeg", "quality": 65,
                                                   "maxWidth": 1280, "maxHeight": 800})
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *args):
        if self.socket:
            with contextlib.suppress(Exception):
                await self.call("Page.stopScreencast")
            await self.socket.close()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)

    async def call(self, method: str, params=None):
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
                    # Acknowledge without waiting on this same reader task.
                    self.next_id += 1
                    await self.socket.send(json.dumps({"id": self.next_id, "method": "Page.screencastFrameAck",
                                                       "params": {"sessionId": frame["sessionId"]}}))
                    if self.frames.full():
                        self.frames.get_nowait()
                    self.frames.put_nowait(frame)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Browser connection closed."))

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
                      "y": self.number(event.get("y"), 0, 800), "button": button,
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
        else:
            raise ValueError("Unsupported viewer input.")
