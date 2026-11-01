"""Opt-in local Chrome checks; no external accounts or model calls."""

import asyncio
import base64
import io
import os
import selectors
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from PIL import Image

from agent.modules.browser.driver import CDPViewer, StagehandDriver

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
async def test_live_stream_paces_animation_and_keeps_final_static_paint(tmp_path):
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
            assert 2 <= count <= 2 / viewer.FRAME_INTERVAL + 5
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
