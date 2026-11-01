"""Opt-in local Chrome checks; no external accounts or model calls."""

import asyncio
import base64
import io
import json
import os
import selectors
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from PIL import Image

from agent.modules.browser.driver import CDPViewer, StagehandDriver
from agent.modules.browser.service import BrowserSession

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(900),
    pytest.mark.skipif(os.getenv("K41_TEST_BROWSER") != "1", reason="Set K41_TEST_BROWSER=1 to test installed Chrome"),
]


class PageHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'''<!doctype html><title>Browser fixture</title>
        <label>Name<input id="name" autocomplete="off"></label>
        <button onclick="localStorage.setItem('login','yes');document.cookie='login=yes;max-age=3600;path=/';document.title='Signed in'">Login</button>
        <script>document.body.dataset.ready='yes';</script>''')


@pytest.mark.asyncio
async def test_local_chrome_snapshot_actions_viewer_and_persistent_login(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    profile = tmp_path / "profile"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    driver = None
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        driver = await StagehandDriver.launch(profile, artifacts)
        assert await driver.facade.run(code=f"await page.goto('{url}'); return await page.title();") == "Browser fixture"
        snapshot = await driver.facade.snapshot(include_iframes=True)
        assert "Login" in snapshot and "Name" in snapshot
        await driver.facade.run(code="await page.locator('#name').focus();")
        tabs = await driver.tabs()
        async with CDPViewer(driver, tabs[0]["id"]) as viewer:
            frame = await asyncio.wait_for(viewer.frames.get(), 10)
            assert frame["data"]
            await viewer.input({"type": "text", "text": "Unicode \u03bb \u65e5\u672c\u8a9e"})
        assert await driver.facade.run(code="return await page.locator('#name').inputValue();") == "Unicode \u03bb \u65e5\u672c\u8a9e"
        await driver.facade.run(code="await page.getByRole('button', {name:'Login'}).click();")
        image = await driver.facade.screenshot(type="jpeg", quality=40)
        assert image["mimeType"] == "image/jpeg"
        await driver.close()
        driver = await StagehandDriver.launch(profile, artifacts)
        assert await driver.facade.run(code=f"await page.goto('{url}'); return await page.evaluate(() => localStorage.getItem('login'));") == "yes"
        assert "login=yes" in await driver.facade.run(code="return await page.evaluate(() => document.cookie);")
        await driver.facade.run(code=f"const other = await context.newPage(); await other.goto('{url}/second');")
        tabs = await driver.tabs()
        second = next(tab for tab in tabs if tab["url"].endswith("/second"))
        await driver.reset_facade(artifacts, second["id"])
        assert await driver.active_tab_id() == second["id"]
        assert await driver.facade.run(code="return await page.url();") == f"{url}/second"
        await driver.facade.run(code="await browser.close();")
        assert "Login" in await driver.facade.snapshot()
    finally:
        if driver:
            await driver.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows selector-loop compatibility")
def test_windows_server_selector_loop_can_drive_chrome(tmp_path):
    async def check():
        driver = await StagehandDriver.launch(tmp_path / "profile", tmp_path / "artifacts")
        try:
            assert isinstance(await driver.facade.run(code="return await page.title();"), str)
        finally:
            await driver.close()

    asyncio.run(check(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.asyncio
async def test_live_stream_keeps_latest_frames_and_final_static_paint(tmp_path):
    driver = await StagehandDriver.launch(tmp_path / "profile", tmp_path / "artifacts")
    try:
        tab = (await driver.tabs())[0]["id"]
        async with CDPViewer(driver, tab) as viewer:
            await viewer.call("Runtime.evaluate", {"expression": """
                document.body.style.margin = '0';
                document.body.innerHTML = '<canvas id="animation" width="1280" height="800"></canvas>';
                window.streamContext = document.querySelector('canvas').getContext('2d');
                window.streamAnimating = true;
                let frameIndex = 0;
                function draw() {
                    if (!window.streamAnimating) return;
                    streamContext.fillStyle = `hsl(${frameIndex++ % 360}, 60%, 50%)`;
                    streamContext.fillRect(0, 0, 1280, 800);
                    requestAnimationFrame(draw);
                }
                draw();
            """})
            start = time.monotonic()
            count = 0
            while time.monotonic() - start < 2:
                try:
                    await asyncio.wait_for(viewer.frames.get(), 0.3)
                    count += 1
                except TimeoutError:
                    pass
            assert count >= 2
            await viewer.call("Runtime.evaluate", {"expression": """
                window.streamAnimating = false;
                streamContext.fillStyle = 'rgb(0, 255, 0)';
                streamContext.fillRect(0, 0, 1280, 800);
            """})
            deadline = time.monotonic() + 3
            while True:
                frame = await asyncio.wait_for(viewer.frames.get(), max(0.01, deadline - time.monotonic()))
                pixel = Image.open(io.BytesIO(base64.b64decode(frame["data"]))).convert("RGB").getpixel((100, 100))
                if pixel[0] < 10 and pixel[1] > 240 and pixel[2] < 10:
                    break
            # Capturing a final paint must not turn a static page into polling.
            await asyncio.sleep(0.5)
            while not viewer.frames.empty():
                viewer.frames.get_nowait()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(viewer.frames.get(), 0.5)
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_live_capture_pause_resume_scaling_and_target_notifications(tmp_path):
    driver = await StagehandDriver.launch(tmp_path / "profile", tmp_path / "artifacts")
    changed = asyncio.Event()
    observer = asyncio.create_task(driver.watch_tabs(changed.set))
    try:
        await asyncio.wait_for(changed.wait(), 5)
        changed.clear()
        tab = (await driver.tabs())[0]["id"]
        async with CDPViewer(driver, tab) as viewer:
            await asyncio.wait_for(viewer.frames.get(), 5)
            await viewer.call("Runtime.evaluate", {"expression": "document.title = 'Target notification'"})
            await asyncio.wait_for(changed.wait(), 5)
            await viewer.configure(visible=False, width=640, quality=45, every_nth_frame=2)
            await viewer.call("Runtime.evaluate", {"expression": """
                document.body.style.background = 'rgb(0,255,0)';
                document.body.innerHTML = '<button style="position:absolute;left:1000px;top:100px;width:100px;height:100px" onclick="document.body.dataset.clicked=1">Click</button>';
            """})
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(viewer.frames.get(), 0.3)
            await viewer.configure(visible=True, width=640, quality=45, every_nth_frame=2)
            frame = await asyncio.wait_for(viewer.frames.get(), 5)
            image = Image.open(io.BytesIO(base64.b64decode(frame["data"]))).convert("RGB")
            viewport = await viewer.call("Runtime.evaluate", {
                "expression": "({width: innerWidth, height: innerHeight})", "returnByValue": True})
            dimensions = viewport["result"]["value"]
            assert image.width == 640
            assert abs(image.height - dimensions["height"] * 640 / dimensions["width"]) <= 1
            pixel = image.getpixel((50, 50))
            assert pixel[0] < 15 and pixel[1] > 240 and pixel[2] < 15
            assert frame["metadata"]["deviceWidth"] == dimensions["width"]
            for kind in ["mousePressed", "mouseReleased"]:
                await viewer.input({"type": kind, "x": 1050, "y": 150, "button": "left", "clickCount": 1})
            result = await viewer.call("Runtime.evaluate", {"expression": "document.body.dataset.clicked"})
            assert result["result"]["value"] == "1"
            viewport = await viewer.call("Runtime.evaluate", {"expression": "window.innerWidth"})
            assert viewport["result"]["value"] == dimensions["width"]
            await viewer.configure(visible=False, width=640)
            await viewer.call("Runtime.evaluate", {"expression": """
                document.body.innerHTML = '';
                document.body.style.height = '2400px';
                document.body.style.background = 'linear-gradient(to bottom, red 0px, red 1000px, lime 1000px, lime 2400px)';
                window.scrollTo(0,1200);
            """})
            await viewer.configure(visible=True, width=640)
            frame = await asyncio.wait_for(viewer.frames.get(), 5)
            pixel = Image.open(io.BytesIO(base64.b64decode(frame["data"]))).convert("RGB").getpixel((10, 10))
            assert pixel[0] < 15 and pixel[1] > 240 and pixel[2] < 15
    finally:
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
        await driver.close()


@pytest.mark.asyncio
async def test_live_dashboard_stream_resumes_after_normal_and_rapid_visibility_changes(tmp_path):
    from agent.delivery.http.dashboard.routes.browser import view_tab

    class ViewerSocket:
        def __init__(self):
            self.events = asyncio.Queue()
            self.frames = asyncio.Queue()
            self.messages = asyncio.Queue()

        async def receive_text(self):
            return json.dumps(await self.events.get())

        async def send_json(self, value):
            self.messages.put_nowait(value)

        async def send_bytes(self, value):
            self.frames.put_nowait(value)

        async def resumed(self, revision):
            while True:
                message = await asyncio.wait_for(self.messages.get(), 5)
                if message.get("visibility_revision") == revision and message.get("paused") is False:
                    return

    driver = await StagehandDriver.launch(tmp_path / "profile", tmp_path / "artifacts")
    task = None
    try:
        tab = (await driver.tabs())[0]["id"]
        current = BrowserSession("live", "admin:1", "profile", driver)
        socket = ViewerSocket()
        task = asyncio.create_task(view_tab(socket, None, current, tab, "viewer", False))
        await asyncio.wait_for(socket.frames.get(), 5)
        socket.events.put_nowait({"type": "visibility", "visible": False, "revision": 1})
        while True:
            message = await asyncio.wait_for(socket.messages.get(), 5)
            if message.get("visibility_revision") == 1:
                break
        while not socket.frames.empty():
            socket.frames.get_nowait()
        await driver.facade.run(code="await page.evaluate(() => { document.body.style.background = 'lime'; });")
        socket.events.put_nowait({"type": "visibility", "visible": True, "revision": 2})
        await socket.resumed(2)
        payload = await asyncio.wait_for(socket.frames.get(), 5)
        pixel = Image.open(io.BytesIO(payload[12:])).convert("RGB").getpixel((10, 10))
        assert pixel[0] < 15 and pixel[1] > 240 and pixel[2] < 15
        while not socket.frames.empty():
            socket.frames.get_nowait()
        socket.events.put_nowait({"type": "visibility", "visible": False, "revision": 3})
        socket.events.put_nowait({"type": "visibility", "visible": True, "revision": 4})
        await socket.resumed(4)
        await asyncio.wait_for(socket.frames.get(), 5)
        assert not task.done()
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await driver.close()
