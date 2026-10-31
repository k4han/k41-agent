"""Opt-in local Chrome checks; no external accounts or model calls."""

import asyncio
import os
import selectors
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

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
