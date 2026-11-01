"""CDP stream lifecycle and client backpressure regression checks."""

import asyncio
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from agent.modules.browser import driver as transport


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.acks = asyncio.Queue()
        self.commands = []
        self.layout = {}
        image = io.BytesIO()
        Image.new("RGB", (1280, 800), "green").save(image, format="JPEG")
        self.snapshot_data = base64.b64encode(image.getvalue()).decode()

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return json.dumps(message)

    async def send(self, raw):
        message = json.loads(raw)
        self.commands.append(message)
        if message["method"] == "Page.screencastFrameAck":
            self.acks.put_nowait((message["params"]["sessionId"], asyncio.get_running_loop().time()))
        result = ({"data": self.snapshot_data} if message["method"] == "Page.captureScreenshot"
                  else self.layout if message["method"] == "Page.getLayoutMetrics" else {})
        self.incoming.put_nowait({"id": message["id"], "result": result})

    async def close(self):
        self.incoming.put_nowait(None)

    def frame(self, session_id):
        self.incoming.put_nowait({"method": "Page.screencastFrame", "params": {
            "sessionId": session_id, "data": str(session_id), "metadata": {},
        }})


@pytest.fixture
def viewer_socket(monkeypatch):
    socket = Socket()

    async def connect(*args, **kwargs):
        return socket

    async def target_url(target_id):
        return "ws://browser/target"

    monkeypatch.setattr(transport, "connect", connect)
    return transport.CDPViewer(SimpleNamespace(target_url=target_url), "target"), socket


@pytest.mark.asyncio
async def test_latest_frame_slot_releases_source_credits_without_waiting_for_network(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        socket.frame(1)
        socket.frame(2)
        await asyncio.wait_for(viewer.call("Input.insertText", {"text": "hello"}), 0.1)
        assert viewer.frames.qsize() == 1
        assert (await viewer.frames.get())["data"] == "2"
        first, second = [await asyncio.wait_for(socket.acks.get(), 1) for _ in range(2)]
        assert [first[0], second[0]] == [1, 2]
        socket.frame(3)
        await viewer.call("Input.insertText", {"text": "still responsive"})
        assert (await asyncio.wait_for(socket.acks.get(), 1))[0] == 3
        assert (await viewer.frames.get())["data"] == "3"
    assert viewer.reader.done()
    assert viewer.snapshotter.done()


@pytest.mark.asyncio
async def test_reader_disconnect_notifies_viewer_and_cleanup_does_not_wait_for_commands(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        await socket.close()
        await asyncio.wait_for(viewer.wait_closed(), 0.2)
        with pytest.raises(ConnectionError):
            await viewer.call("Input.insertText", {"text": "hello"})
    assert all(command["method"] != "Page.stopScreencast" for command in socket.commands)


@pytest.mark.asyncio
async def test_ack_failure_notifies_viewer(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        original_send = socket.send

        async def send(raw):
            if json.loads(raw)["method"] == "Page.screencastFrameAck":
                raise ConnectionError("CDP disconnected")
            await original_send(raw)

        socket.send = send
        socket.frame(1)
        await asyncio.wait_for(viewer.frames.get(), 1)
        await asyncio.wait_for(viewer.wait_closed(), 1)
    assert isinstance(viewer.reader.exception(), ConnectionError)


@pytest.mark.asyncio
async def test_idle_source_flushes_final_paint_once_without_restarting_static_capture(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        socket.frame(1)
        assert (await asyncio.wait_for(viewer.frames.get(), 1))["data"] == "1"
        assert (await asyncio.wait_for(viewer.frames.get(), 1))["data"] == socket.snapshot_data
        # A screenshot can itself cause a screencast event. Its unchanged image
        # must not schedule another trailing screenshot, even if the screenshot
        # encoder produced different JPEG bytes.
        socket.incoming.put_nowait({"method": "Page.screencastFrame", "params": {
            "sessionId": 2, "data": "1", "metadata": {},
        }})
        await asyncio.wait_for(viewer.frames.get(), 1)
        await asyncio.wait_for(socket.acks.get(), 1)
        await asyncio.wait_for(socket.acks.get(), 1)
        await asyncio.sleep(0.25)
        assert sum(command["method"] == "Page.captureScreenshot" for command in socket.commands) == 1


@pytest.mark.asyncio
async def test_hidden_viewer_stops_capture_and_resume_captures_scaled_fresh_image(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        socket.frame(1)
        await asyncio.wait_for(viewer.frames.get(), 1)
        await viewer.configure(visible=False, width=640, quality=45)
        commands = len(socket.commands)
        socket.frame(2)
        await asyncio.sleep(0.15)
        assert viewer.frames.empty()
        assert not any(command["method"] == "Page.captureScreenshot" for command in socket.commands[commands:])
        await viewer.configure(visible=True, width=640, quality=45)
        frame = await asyncio.wait_for(viewer.frames.get(), 1)
        assert Image.open(io.BytesIO(base64.b64decode(frame["data"]))).size == (640, 400)
        assert frame["metadata"]["deviceWidth"] == 1280
        start = next(command for command in reversed(socket.commands) if command["method"] == "Page.startScreencast")
        assert start["params"]["maxWidth"] == 640
        assert start["params"]["quality"] == 45
        snapshot = next(command for command in reversed(socket.commands) if command["method"] == "Page.captureScreenshot")
        assert "clip" not in snapshot["params"]


@pytest.mark.asyncio
async def test_fresh_capture_uses_current_scroll_position_instead_of_old_source_metadata(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        await viewer.configure(visible=False, width=640)
        socket.layout = {"cssVisualViewport": {"pageX": 0, "pageY": 1200, "clientWidth": 1280, "clientHeight": 800}}
        await viewer.configure(visible=True, width=640)
        frame = await asyncio.wait_for(viewer.frames.get(), 1)
        assert frame["metadata"]["scrollOffsetY"] == 1200
        snapshot = next(command for command in reversed(socket.commands) if command["method"] == "Page.captureScreenshot")
        assert "clip" not in snapshot["params"]


@pytest.mark.parametrize("width,height,quality,expected", [
    (800, 600, 1, (800, 500)), (1024, 600, 100, (960, 600)),
    (1280, 720, 65, (1152, 720)), (1600, 900, 80, (1280, 800)),
    (1920, 1080, 90, (1280, 800)), (1280, 100, 95, (160, 100)),
    (100, 800, 35, (100, 63)), (1280, 800, 65, (1280, 800)),
])
@pytest.mark.asyncio
async def test_capture_respects_independent_caps_and_preserves_input_dimensions(viewer_socket, width, height, quality, expected):
    viewer, socket = viewer_socket
    async with viewer:
        await viewer.configure(width=width, height=height, quality=quality, refresh=True)
        frame = await asyncio.wait_for(viewer.frames.get(), 1)
        image = Image.open(io.BytesIO(base64.b64decode(frame["data"])))
        assert image.size == expected
        assert image.width <= width and image.height <= height
        assert (frame["metadata"]["deviceWidth"], frame["metadata"]["deviceHeight"]) == (1280, 800)
        assert viewer.stream_options["maxWidth"] == width
        assert viewer.stream_options["maxHeight"] == height
        snapshot = next(command for command in reversed(socket.commands) if command["method"] == "Page.captureScreenshot")
        assert snapshot["params"]["quality"] == quality
        assert not any(command["method"].startswith("Emulation.") for command in socket.commands)
