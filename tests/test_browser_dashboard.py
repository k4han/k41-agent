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
            event, self.event = self.event, None
            if event is None:
                await asyncio.Event().wait()
            return event

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


class StreamSocket:
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


def stream_fixture(monkeypatch, viewer=None):
    from agent.modules.browser.service import BrowserSession

    viewer = viewer or FakeViewer()
    monkeypatch.setattr(routes, "CDPViewer", lambda *args: viewer)

    class Driver:
        async def tabs(self):
            return [{"id": "first"}, {"id": "second"}]

    current = BrowserSession("thread", "admin:1", "profile", Driver())
    current.last_activity = 42
    return current, viewer, StreamSocket()


def stream_frame():
    return {"data": base64.b64encode(b"jpeg").decode(), "metadata": {"deviceWidth": 1280, "deviceHeight": 800}}


@pytest.mark.asyncio
async def test_frame_window_is_bounded_and_late_acks_cannot_release_another_tab(monkeypatch):
    current, viewer, socket = stream_fixture(monkeypatch)
    frame_ids = count()
    state = routes.BrowserStreamState(frame_ids=frame_ids)
    task = asyncio.create_task(routes.view_tab(socket, None, current, "first", "viewer", False, state=state))
    try:
        for frame_id in range(1, 4):
            viewer.frames.put_nowait(stream_frame())
            payload = await asyncio.wait_for(socket.frames.get(), 1)
            assert struct.unpack("!III", payload[:12]) == (frame_id, 1280, 800)
            assert payload[12:] == b"jpeg"
        viewer.frames.put_nowait(stream_frame())
        for frame_id in [True, 0, 4, -1]:
            socket.events.put_nowait({"type": "frame_ack", "frame_id": frame_id})
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.frames.get(), 0.05)
        socket.events.put_nowait({"type": "frame_ack", "frame_id": 2})
        payload = await asyncio.wait_for(socket.frames.get(), 1)
        assert struct.unpack("!III", payload[:12])[0] == 4
        assert len(state.pending) == 3
        assert current.controller is None and current.last_activity == 42
        socket.events.put_nowait({"type": "select_tab", "tab_id": "second"})
        assert await asyncio.wait_for(task, 1) == ("second", False)
        _, viewer, _ = stream_fixture(monkeypatch)
        task = asyncio.create_task(routes.view_tab(socket, None, current, "second", "viewer", False, state=state))
        for frame_id in range(5, 8):
            viewer.frames.put_nowait(stream_frame())
            assert struct.unpack("!III", (await asyncio.wait_for(socket.frames.get(), 1))[:12])[0] == frame_id
        viewer.frames.put_nowait(stream_frame())
        socket.events.put_nowait({"type": "frame_ack", "frame_id": 4})
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.frames.get(), 0.05)
        socket.events.put_nowait({"type": "frame_ack", "frame_id": 5, "drawn": False})
        assert struct.unpack("!III", (await asyncio.wait_for(socket.frames.get(), 1))[:12])[0] == 8
        assert state.frames_dropped == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not current.subscribers


@pytest.mark.asyncio
async def test_slow_input_does_not_block_acks_heartbeats_or_visibility(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowViewer(FakeViewer):
        async def input(self, event):
            entered.set()
            await release.wait()

    current, viewer, socket = stream_fixture(monkeypatch, SlowViewer())
    current.handoff_id = "handoff"
    state = routes.BrowserStreamState()
    task = asyncio.create_task(routes.view_tab(socket, None, current, "first", "viewer", False, state=state))
    try:
        await asyncio.wait_for(socket.messages.get(), 1)
        viewer.frames.put_nowait(stream_frame())
        await asyncio.wait_for(socket.frames.get(), 1)
        socket.events.put_nowait({"type": "text", "text": "private", "input_id": 1})
        await asyncio.wait_for(entered.wait(), 1)
        socket.events.put_nowait({"type": "frame_ack", "frame_id": 1})
        socket.events.put_nowait({"type": "ping", "id": 9})
        while (message := await asyncio.wait_for(socket.messages.get(), 0.5))["type"] != "pong":
            pass
        assert message["id"] == 9 and not state.pending
        socket.events.put_nowait({"type": "visibility", "visible": False})
        while state.visible:
            await asyncio.sleep(0)
        assert current.handoff_id == "handoff"
        release.set()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_pause_sends_no_images_and_resume_requests_fresh_capture(monkeypatch):
    class ConfigurableViewer(FakeViewer):
        def __init__(self):
            super().__init__()
            self.configurations = asyncio.Queue()

        async def configure(self, **values):
            self.configurations.put_nowait(values)
            if values["visible"]:
                while not self.frames.empty():
                    self.frames.get_nowait()
                self.frames.put_nowait(stream_frame())

    current, viewer, socket = stream_fixture(monkeypatch, ConfigurableViewer())
    state = routes.BrowserStreamState(visible=False)
    task = asyncio.create_task(routes.view_tab(socket, None, current, "first", "viewer", False, state=state))
    try:
        assert not (await asyncio.wait_for(viewer.configurations.get(), 1))["visible"]
        viewer.frames.put_nowait(stream_frame())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.frames.get(), 0.05)
        socket.events.put_nowait({"type": "visibility", "visible": True, "width": 640})
        configuration = await asyncio.wait_for(viewer.configurations.get(), 1)
        assert configuration["visible"] and configuration["width"] == 640
        await asyncio.wait_for(socket.frames.get(), 1)
        assert current.last_activity == 42 and current.controller is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_session_events_push_controller_and_tab_changes_without_polling(monkeypatch):
    current, viewer, socket = stream_fixture(monkeypatch)
    task = asyncio.create_task(routes.view_tab(socket, None, current, "first", "viewer", False))
    try:
        initial = await asyncio.wait_for(socket.messages.get(), 1)
        current.handoff_id = "handoff"
        current.controller = "another-window"
        current.notify()
        changed = await asyncio.wait_for(socket.messages.get(), 0.5)
        assert changed["controller"] == "other" and not changed["can_control"]
        assert changed["sequence"] > initial["sequence"]
        current.controller = None
        current.notify()
        changed = await asyncio.wait_for(socket.messages.get(), 0.5)
        assert changed["controller"] == "available" and changed["can_control"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_input_queue_coalesces_motion_and_preserves_discrete_order():
    from agent.delivery.http.dashboard.routes.helpers.browser_stream import BrowserInputQueue

    queue = BrowserInputQueue()
    queue.put({"type": "mousePressed"}, "handoff", 0)
    for x in range(100):
        queue.put({"type": "mouseMoved", "x": x}, "handoff", 0)
    for delta in [10, 20, -5]:
        queue.put({"type": "mouseWheel", "deltaY": delta}, "handoff", 0)
    queue.put({"type": "mouseReleased"}, "handoff", 0)
    assert len(queue.items) == 4
    assert [(await queue.get())[0] for _ in range(4)] == [
        {"type": "mousePressed"}, {"type": "mouseMoved", "x": 99},
        {"type": "mouseWheel", "deltaX": 0, "deltaY": 25}, {"type": "mouseReleased"},
    ]
    for _ in range(64):
        queue.put({"type": "text", "text": "a"}, "handoff", 0)
    with pytest.raises(ValueError, match="queue is full"):
        queue.put({"type": "text", "text": "b"}, "handoff", 0)


def test_stream_quality_adapts_to_slow_acks_and_recovers_with_hysteresis(monkeypatch):
    from agent.delivery.http.dashboard.routes.helpers import browser_stream

    now = 10.0
    monkeypatch.setattr(browser_stream.time, "monotonic", lambda: now)
    state = routes.BrowserStreamState()
    state.pending[1] = now - 0.4
    state.acknowledge({"frame_id": 1, "render_ms": 30})
    assert (state.quality, state.every_nth_frame) == (45, 3)
    assert state.settings_changed.is_set()
    for frame_id in range(2, 20):
        now += 2.1
        state.pending[frame_id] = now - 0.02
        state.acknowledge({"frame_id": frame_id, "render_ms": 5})
    assert (state.quality, state.every_nth_frame) == (65, 1)
    state.resize(50000)
    assert state.width == 1280
    state.resize(float("nan"))
    assert state.width == 1280
