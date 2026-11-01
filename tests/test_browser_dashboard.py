import asyncio
import base64
import json
import struct
from contextlib import asynccontextmanager
from itertools import count
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent.bootstrap.app import ContainerActivationMiddleware
from agent.delivery.http.dashboard.routes import browser as routes
from agent.modules.admin_auth import get_current_admin
from agent.modules.browser.service import BrowserService
from agent.shared.infrastructure.csrf_protection import CSRFProtectionMiddleware
from agent.shared.infrastructure.db.engine import initialize_async_engine, close_async_engine
from test_browser_sessions import FakeDriver, make_browser_metadata


def test_browser_setup_requires_authentication_and_csrf(browser_client, monkeypatch):
    client, _ = browser_client
    monkeypatch.setattr(routes, "installation_unavailable_reason", lambda: None)
    client.headers.pop("X-CSRF-Token")
    assert client.post("/dashboard-api/browser/setup", json={"action": "install"}).status_code == 403
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    client.headers.pop("X-Test-Admin")
    assert client.get("/dashboard-api/browser/setup").status_code == 401
    assert client.get("/dashboard-api/browser/configuration").status_code == 401
    assert client.post("/dashboard-api/browser/setup", json={"action": "check"}).status_code == 401


def test_browser_setup_reports_configured_binary_and_rejects_unsupported_install(browser_client, monkeypatch):
    client, browser = browser_client
    monkeypatch.setattr(browser.container.config_service, "get_str", lambda *args: "/missing/custom-chrome")
    monkeypatch.setattr(routes, "find_browser", lambda: "/detected/chrome")
    monkeypatch.setattr(routes, "installation_unavailable_reason", lambda: "This platform is unsupported")
    status = client.get("/dashboard-api/browser/setup").json()
    assert status["executable_path"] == "/missing/custom-chrome"
    assert status["installed"] is False
    assert status["installation_supported"] is False
    assert client.post("/dashboard-api/browser/setup", json={"action": "install"}).status_code == 400
    assert client.post("/dashboard-api/browser/setup", json={"action": "shell"}).status_code == 422
    configuration = client.get("/dashboard-api/browser/configuration").json()
    assert "browser.executable_path" in configuration["settings"]
    assert all(key.startswith("browser.") for key in configuration["settings"])


@pytest.mark.parametrize("action", ["install", "repair", "check"])
def test_browser_setup_returns_background_job_and_surfaces_conflicts(browser_client, monkeypatch, action):
    client, browser = browser_client
    captured = []
    monkeypatch.setattr(routes, "installation_unavailable_reason", lambda: None)
    monkeypatch.setattr(browser.container.config_service, "get_str", lambda *args: "/custom/chrome")

    class Manager:
        def start(self, action, executable_path):
            if captured:
                raise routes.BrowserSetupBusyError("Setup is busy")
            captured.append((action, executable_path))
            return self.status()

        def status(self):
            return {"id": "job", "state": "running", "action": action}

    monkeypatch.setattr(routes, "setup_manager", lambda request: Manager())
    response = client.post("/dashboard-api/browser/setup", json={"action": action})
    assert response.status_code == 202
    assert response.json()["state"] == "running"
    assert captured == [(action, "/custom/chrome")]
    assert client.get("/dashboard-api/browser/setup").json()["job"]["id"] == "job"
    assert client.post("/dashboard-api/browser/setup", json={"action": "check"}).status_code == 409


@pytest.fixture
def browser_client(isolated_container, tmp_path):
    async def launch(*args):
        return FakeDriver()

    browser = BrowserService(isolated_container, root=tmp_path / "browser", launcher=launch)
    isolated_container._browser_service = browser

    @asynccontextmanager
    async def lifespan(app):
        await initialize_async_engine(metadata=make_browser_metadata(), container=isolated_container)
        yield
        await browser.shutdown()
        await close_async_engine(container=isolated_container)

    app = FastAPI(lifespan=lifespan)
    app.state.container = isolated_container
    app.add_middleware(ContainerActivationMiddleware, container=isolated_container)
    app.add_middleware(CSRFProtectionMiddleware)
    app.include_router(routes.router)
    app.include_router(routes.viewer_router)

    async def admin(request: Request):
        value = request.headers.get("X-Test-Admin")
        if not value:
            raise HTTPException(401, "Not authenticated")
        return value

    app.dependency_overrides[get_current_admin] = admin
    with TestClient(app) as client:
        client.headers["X-Test-Admin"] = "1"
        client.get("/dashboard-api/browser/profiles")
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        yield client, browser


def test_browser_profile_api_enforces_authentication_csrf_and_ownership(browser_client):
    client, browser = browser_client
    client.headers.pop("X-CSRF-Token")
    assert client.post("/dashboard-api/browser/profiles", json={"name": "Personal"}).status_code == 403
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    profile = client.post("/dashboard-api/browser/profiles", json={"name": "Personal"}).json()
    assert client.put("/dashboard-api/browser/threads/first", json={"profile_id": profile["profile_id"]}).status_code == 200
    assert client.post("/dashboard-api/browser/threads/first").status_code == 200
    assert client.delete(f"/dashboard-api/browser/profiles/{profile['profile_id']}").status_code == 409
    client.headers["X-Test-Admin"] = "2"
    assert client.get("/dashboard-api/browser/profiles").json() == {"profiles": []}
    assert client.get("/dashboard-api/browser/threads/first").status_code == 403
    assert client.post("/dashboard-api/browser/threads/first/viewer-ticket").status_code == 403
    assert client.get("/dashboard-api/browser/artifacts/missing").status_code == 404
    client.headers.pop("X-Test-Admin")
    assert client.get("/dashboard-api/browser/profiles").status_code == 401


class FakeViewer:
    inputs = []

    def __init__(self, *args):
        self.frames = asyncio.Queue()
        self.closed = asyncio.Event()
        self.acknowledged = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def activate(self):
        pass

    async def wait_closed(self):
        await self.closed.wait()

    async def input(self, event):
        self.inputs.append(event)

    def acknowledge_frame(self):
        self.acknowledged.put_nowait(True)


def test_viewer_tickets_origins_and_agent_control(browser_client, monkeypatch):
    client, browser = browser_client
    monkeypatch.setattr(routes, "CDPViewer", FakeViewer)
    FakeViewer.inputs = []
    assert client.post("/dashboard-api/browser/threads/chat/viewer-ticket").status_code == 409
    client.post("/dashboard-api/browser/threads/chat")
    ticket = client.post("/dashboard-api/browser/threads/chat/viewer-ticket").json()["ticket"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/dashboard-api/browser/viewer", headers={"Origin": "https://untrusted.example"}):
            pass
    with client.websocket_connect("/dashboard-api/browser/viewer", headers={"Origin": "http://testserver"}) as ws:
        ws.send_json({"ticket": ticket})
        assert ws.receive_json()["mode"] == "agent"
        ws.send_json({"type": "text", "text": "private input"})
        assert ws.receive_json()["type"] == "error"
    assert FakeViewer.inputs == []
    with client.websocket_connect("/dashboard-api/browser/viewer", headers={"Origin": "http://testserver"}) as ws:
        ws.send_json({"ticket": ticket})
        assert ws.receive_json()["type"] == "error"


def test_only_one_human_controller_and_disconnect_preserves_handoff(browser_client, monkeypatch):
    client, browser = browser_client
    monkeypatch.setattr(routes, "CDPViewer", FakeViewer)
    FakeViewer.inputs = []
    client.post("/dashboard-api/browser/threads/chat")
    ticket = client.post("/dashboard-api/browser/threads/chat/viewer-ticket").json()["ticket"]
    session = next(iter(browser.sessions.values()))
    session.handoff_id = "pending"
    with client.websocket_connect("/dashboard-api/browser/viewer", headers={"Origin": "http://testserver"}) as first:
        first.send_json({"ticket": ticket})
        assert first.receive_json()["can_control"]
        first.send_json({"type": "text", "text": "login"})
        # The next status confirms the first input was processed and claimed the controller.
        first.receive_json()
        second_ticket = client.post("/dashboard-api/browser/threads/chat/viewer-ticket").json()["ticket"]
        with client.websocket_connect("/dashboard-api/browser/viewer", headers={"Origin": "http://testserver"}) as second:
            second.send_json({"ticket": second_ticket})
            assert not second.receive_json()["can_control"]
            second.send_json({"type": "text", "text": "blocked"})
            assert second.receive_json()["type"] == "error"
    assert session.handoff_id == "pending"
    assert FakeViewer.inputs == [{"type": "text", "text": "login"}]


def test_viewer_closes_socket_when_cdp_reader_stops(browser_client, monkeypatch):
    client, _ = browser_client

    class DisconnectedViewer(FakeViewer):
        async def __aenter__(self):
            self.closed.set()
            return self

    monkeypatch.setattr(routes, "CDPViewer", DisconnectedViewer)
    client.post("/dashboard-api/browser/threads/chat")
    ticket = client.post("/dashboard-api/browser/threads/chat/viewer-ticket").json()["ticket"]
    with client.websocket_connect("/dashboard-api/browser/viewer", headers={"Origin": "http://testserver"}) as ws:
        ws.send_json({"ticket": ticket})
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()


@pytest.mark.asyncio
async def test_viewer_follows_agent_and_preserves_manually_pinned_tabs(monkeypatch):
    monkeypatch.setattr(routes, "CDPViewer", FakeViewer)
    tabs = [{"id": "first"}, {"id": "second"}]

    class Driver:
        active = "second"

        async def tabs(self):
            return tabs

        async def active_tab_id(self):
            return self.active

    current = SimpleNamespace(driver=Driver(), closed=False, handoff_id=None, active_tab_id=None,
                              controller=None, lock=asyncio.Lock())

    class Socket:
        def __init__(self, event):
            self.event = event
            self.statuses = []

        async def receive_text(self):
            # Let the initial status run before a user changes the selection.
            while not self.statuses:
                await asyncio.sleep(0)
            return self.event

        async def send_json(self, value):
            self.statuses.append(value)

    # The sender, rather than user input, changes the watched target.
    assert await asyncio.wait_for(routes.view_tab(Socket('{}'), None, current, "first", "viewer"), 1) == ("second", True)
    pinned = Socket('{"type":"select_tab","tab_id":"first"}')
    assert await routes.view_tab(pinned, None, current, "second", "viewer") == ("first", False)
    pinned = Socket('{"type":"follow_agent"}')
    assert await routes.view_tab(pinned, None, current, "first", "viewer", False) == ("second", True)
    assert pinned.statuses[0]["tab_id"] == "first"
    assert not pinned.statuses[0]["follow_agent"]
    assert pinned.statuses[0]["reset"]

    # A pin cannot leave the stream attached to a target that no longer exists.
    tabs.pop(0)
    assert await asyncio.wait_for(routes.view_tab(Socket('{}'), None, current, "first", "viewer", False), 1) == ("second", False)


@pytest.mark.asyncio
async def test_static_viewer_does_not_disconnect_without_new_frames(monkeypatch):
    monkeypatch.setattr(routes, "CDPViewer", FakeViewer)
    status_sent = asyncio.Event()

    class Driver:
        async def tabs(self):
            return [{"id": "first"}]

    class Socket:
        async def receive_text(self):
            await asyncio.Event().wait()

        async def send_json(self, value):
            status_sent.set()

    current = SimpleNamespace(driver=Driver(), closed=False, handoff_id=None, active_tab_id=None, controller=None)
    task = asyncio.create_task(routes.view_tab(Socket(), None, current, "first", "viewer"))
    try:
        await asyncio.wait_for(status_sent.wait(), 1)
        await asyncio.sleep(1.1)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_binary_frames_wait_for_matching_ack_without_claiming_control(monkeypatch):
    viewer = FakeViewer()
    monkeypatch.setattr(routes, "CDPViewer", lambda *args: viewer)
    image = b"\xff\xd8jpeg\xff\xd9"

    def frame():
        return {"data": base64.b64encode(image).decode(), "metadata": {"deviceWidth": 1280, "deviceHeight": 800}}

    class Driver:
        async def tabs(self):
            return [{"id": "first"}, {"id": "second"}]

    class Socket:
        def __init__(self):
            self.events = asyncio.Queue()
            self.frames = asyncio.Queue()
            self.statuses = []

        async def receive_text(self):
            return json.dumps(await self.events.get())

        async def send_json(self, value):
            self.statuses.append(value)

        async def send_bytes(self, value):
            self.frames.put_nowait(value)

    current = SimpleNamespace(driver=Driver(), closed=False, handoff_id="handoff", active_tab_id=None,
                              controller=None, lock=asyncio.Lock())
    socket = Socket()
    frame_ids = count()
    task = asyncio.create_task(routes.view_tab(socket, None, current, "first", "viewer", False, frame_ids))
    try:
        viewer.frames.put_nowait(frame())
        first = await asyncio.wait_for(socket.frames.get(), 1)
        assert struct.unpack("!III", first[:12]) == (1, 1280, 800)
        assert first[12:] == image
        viewer.frames.put_nowait(frame())
        for frame_id in [True, 0, 2]:
            socket.events.put_nowait({"type": "frame_ack", "frame_id": frame_id})
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.frames.get(), 0.05)
        assert viewer.acknowledged.empty()
        socket.events.put_nowait({"type": "frame_ack", "frame_id": 1})
        second = await asyncio.wait_for(socket.frames.get(), 1)
        assert struct.unpack("!III", second[:12]) == (2, 1280, 800)
        assert await viewer.acknowledged.get()
        assert current.controller is None
        assert not any(status["type"] == "error" for status in socket.statuses)
        # A tab switch must remain responsive while its last frame awaits ACK.
        socket.events.put_nowait({"type": "select_tab", "tab_id": "second"})
        assert await asyncio.wait_for(task, 1) == ("second", False)
        # Frame IDs span tab streams, so a delayed ACK cannot grant a new credit.
        viewer = FakeViewer()
        current.handoff_id = None
        current.controller = None
        current.last_activity = 42
        task = asyncio.create_task(routes.view_tab(socket, None, current, "second", "viewer", False, frame_ids))
        viewer.frames.put_nowait(frame())
        third = await asyncio.wait_for(socket.frames.get(), 1)
        assert struct.unpack("!III", third[:12])[0] == 3
        viewer.frames.put_nowait(frame())
        socket.events.put_nowait({"type": "frame_ack", "frame_id": 2})
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.frames.get(), 0.05)
        assert viewer.acknowledged.empty()
        socket.events.put_nowait({"type": "frame_ack", "frame_id": 3})
        fourth = await asyncio.wait_for(socket.frames.get(), 1)
        assert struct.unpack("!III", fourth[:12])[0] == 4
        assert current.controller is None
        assert current.last_activity == 42
        assert not any(status["type"] == "error" for status in socket.statuses)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
