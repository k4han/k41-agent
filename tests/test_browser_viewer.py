"""CDP stream lifecycle and client backpressure regression checks."""

import asyncio
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

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
        Image.new("RGB", (1280, 720), "green").save(image, format="JPEG")
        self.snapshot_bytes = image.getvalue()
        self.snapshot_data = base64.b64encode(self.snapshot_bytes).decode()

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
@pytest.mark.parametrize("kind,current_index,entry_id", [
    ("back", 1, 12), ("forward", 0, 34), ("back", 0, None), ("forward", 1, None),
])
async def test_viewer_history_navigation_respects_history_boundaries(viewer_socket, kind, current_index, entry_id):
    viewer, _ = viewer_socket
    viewer.call = AsyncMock(return_value={"currentIndex": current_index, "entries": [{"id": 12}, {"id": 34}]})

    await viewer.input({"type": kind})

    expected = [call("Page.getNavigationHistory")]
    if entry_id is not None:
        expected.append(call("Page.navigateToHistoryEntry", {"entryId": entry_id}))
    assert viewer.call.call_args_list == expected


@pytest.mark.asyncio
async def test_viewer_reload_refreshes_the_current_page(viewer_socket):
    viewer, _ = viewer_socket
    viewer.call = AsyncMock()

    await viewer.input({"type": "reload"})

    viewer.call.assert_awaited_once_with("Page.reload")


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
        assert (await asyncio.wait_for(viewer.frames.get(), 1))["data"] == socket.snapshot_bytes
        # A screenshot can itself cause a screencast event. Its unchanged image
        # must not schedule another trailing screenshot, even if the screenshot
        # encoder produced different JPEG bytes.
        socket.incoming.put_nowait({"method": "Page.screencastFrame", "params": {
            "sessionId": 2, "data": "1", "metadata": {},
        }})
        await asyncio.wait_for(socket.acks.get(), 1)
        await asyncio.wait_for(socket.acks.get(), 1)
        await asyncio.sleep(0.25)
        assert viewer.frames.empty()
        assert sum(command["method"] == "Page.captureScreenshot" for command in socket.commands) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_consumed", [False, True])
async def test_unchanged_feedback_cannot_replace_completed_snapshot(viewer_socket, snapshot_consumed):
    viewer, socket = viewer_socket
    async with viewer:
        socket.frame(1)
        assert (await asyncio.wait_for(viewer.frames.get(), 1))["data"] == "1"
        await viewer.capture_frame()
        if snapshot_consumed:
            frame = await asyncio.wait_for(viewer.frames.get(), 1)
            assert frame["data"] == socket.snapshot_bytes and frame["priority"]

        socket.incoming.put_nowait({"method": "Page.screencastFrame", "params": {
            "sessionId": 2, "data": "1", "metadata": {},
        }})
        acknowledgements = [await asyncio.wait_for(socket.acks.get(), 1) for _ in range(2)]
        assert [ack[0] for ack in acknowledgements] == [1, 2]
        if snapshot_consumed:
            assert viewer.frames.empty()
        else:
            assert viewer.frames.qsize() == 1
            frame = viewer.frames.get_nowait()
            assert frame["data"] == socket.snapshot_bytes and frame["priority"]
        assert viewer.metrics()["source_frames_dropped"] == 1
        assert viewer.metrics()["snapshots_dropped"] == 0


@pytest.mark.asyncio
async def test_new_screencast_supersedes_completed_snapshot(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        socket.frame(1)
        await asyncio.wait_for(viewer.frames.get(), 1)
        await viewer.capture_frame()

        socket.frame(2)
        acknowledgements = [await asyncio.wait_for(socket.acks.get(), 1) for _ in range(2)]
        assert [ack[0] for ack in acknowledgements] == [1, 2]
        frame = await asyncio.wait_for(viewer.frames.get(), 1)
        assert frame["data"] == "2" and not frame.get("priority")
        assert viewer.metrics()["snapshots_dropped"] == 1
        assert viewer.metrics()["source_frames_dropped"] == 0

        await viewer.capture_frame()
        frame = await asyncio.wait_for(viewer.frames.get(), 1)
        assert frame["data"] == socket.snapshot_bytes and frame["priority"]
        assert viewer.metrics()["snapshots"] == 2


@pytest.mark.asyncio
async def test_refresh_replaces_queued_snapshot_and_counts_snapshot_drop(viewer_socket):
    viewer, _ = viewer_socket
    async with viewer:
        await viewer.capture_frame()
        await viewer.capture_frame()
        assert viewer.frames.qsize() == 1
        assert viewer.frames.get_nowait()["priority"]
        assert viewer.metrics()["snapshots"] == 2
        assert viewer.metrics()["snapshots_dropped"] == 1
        assert viewer.metrics()["source_frames_dropped"] == 0


@pytest.mark.asyncio
async def test_refresh_allows_unchanged_source_until_current_snapshot_is_ready(viewer_socket, monkeypatch):
    viewer, socket = viewer_socket
    entered, release = asyncio.Event(), asyncio.Event()

    async def resize(*args):
        entered.set()
        await release.wait()
        return socket.snapshot_bytes, 1280, 720

    async with viewer:
        socket.frame(1)
        await asyncio.wait_for(viewer.frames.get(), 1)
        await viewer.capture_frame()
        previous = await asyncio.wait_for(viewer.frames.get(), 1)
        monkeypatch.setattr(transport.asyncio, "to_thread", resize)
        refresh = asyncio.create_task(viewer.configure(refresh=True))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            socket.incoming.put_nowait({"method": "Page.screencastFrame", "params": {
                "sessionId": 2, "data": "1", "metadata": {},
            }})
            acknowledgements = [await asyncio.wait_for(socket.acks.get(), 1) for _ in range(2)]
            assert [ack[0] for ack in acknowledgements] == [1, 2]
            frame = await asyncio.wait_for(viewer.frames.get(), 1)
            assert frame["data"] == "1"
            assert frame["capture_revision"] > previous["capture_revision"]

            release.set()
            await asyncio.wait_for(refresh, 1)
            snapshot = await asyncio.wait_for(viewer.frames.get(), 1)
            assert snapshot["priority"] and snapshot["data"] == socket.snapshot_bytes
            assert snapshot["capture_revision"] == frame["capture_revision"]
            assert viewer.metrics()["source_frames_dropped"] == 0
            assert viewer.metrics()["snapshots_dropped"] == 0
        finally:
            release.set()
            refresh.cancel()
            await asyncio.gather(refresh, return_exceptions=True)


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
        assert Image.open(io.BytesIO(frame["data"])).size == (640, 360)
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
    (800, 600, 1, (800, 450)), (1024, 600, 100, (1024, 576)),
    (1280, 720, 50, (1280, 720)), (1600, 900, 80, (1280, 720)),
    (1920, 1080, 90, (1280, 720)), (1280, 100, 95, (178, 100)),
    (100, 800, 35, (100, 56)), (1280, 800, 65, (1280, 720)),
])
@pytest.mark.asyncio
async def test_capture_respects_independent_caps_and_preserves_input_dimensions(viewer_socket, width, height, quality, expected):
    viewer, socket = viewer_socket
    async with viewer:
        await viewer.configure(width=width, height=height, quality=quality, refresh=True)
        frame = await asyncio.wait_for(viewer.frames.get(), 1)
        image = Image.open(io.BytesIO(frame["data"]))
        assert image.size == expected
        assert image.width <= width and image.height <= height
        assert (frame["metadata"]["deviceWidth"], frame["metadata"]["deviceHeight"]) == (1280, 720)
        assert viewer.stream_options["maxWidth"] == width
        assert viewer.stream_options["maxHeight"] == height
        snapshot = next(command for command in reversed(socket.commands) if command["method"] == "Page.captureScreenshot")
        assert snapshot["params"]["quality"] == quality
        assert not any(command["method"].startswith("Emulation.") for command in socket.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [False, True])
async def test_viewer_starts_with_final_settings_without_restarting_capture(viewer_socket, visible):
    original, socket = viewer_socket
    viewer = transport.CDPViewer(original.driver, "target", visible=visible, width=640, height=400,
                                 quality=90, every_nth_frame=2)
    async with viewer:
        await viewer.configure(visible=visible, width=640, height=400, quality=90, every_nth_frame=2, refresh=True)
        starts = [command for command in socket.commands if command["method"] == "Page.startScreencast"]
        assert len(starts) == int(visible)
        if visible:
            assert starts[0]["params"] == {"format": "jpeg", "quality": 90, "maxWidth": 640,
                                           "maxHeight": 400, "everyNthFrame": 2}
            frame = await asyncio.wait_for(viewer.frames.get(), 1)
            assert frame["capture_revision"] == viewer.capture_revision
            assert frame["priority"] and frame["captured_at"] > 0
        else:
            assert viewer.frames.empty()
            assert not any(command["method"] == "Page.captureScreenshot" for command in socket.commands)
        assert not any(command["method"] == "Page.stopScreencast" for command in socket.commands)
    assert sum(command["method"] == "Page.stopScreencast" for command in socket.commands) == int(visible)


@pytest.mark.asyncio
async def test_closing_paused_viewer_does_not_stop_screencast_twice(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        await viewer.configure(visible=False)
    assert sum(command["method"] == "Page.stopScreencast" for command in socket.commands) == 1


@pytest.mark.asyncio
async def test_failed_pause_still_stops_screencast_during_cleanup(viewer_socket, monkeypatch):
    viewer, socket = viewer_socket
    original_call = viewer.call
    stop_attempts = 0

    async def call(method, params=None):
        nonlocal stop_attempts
        if method == "Page.stopScreencast":
            stop_attempts += 1
            if stop_attempts == 1:
                raise RuntimeError("Pause interrupted")
        return await original_call(method, params)

    monkeypatch.setattr(viewer, "call", call)
    async with viewer:
        with pytest.raises(RuntimeError, match="Pause interrupted"):
            await viewer.configure(visible=False)
        assert not viewer.visible and viewer.screencast_active
    assert stop_attempts == 2
    assert sum(command["method"] == "Page.stopScreencast" for command in socket.commands) == 1


@pytest.mark.asyncio
async def test_delayed_snapshot_cannot_replace_a_newer_screencast_frame(viewer_socket, monkeypatch):
    viewer, socket = viewer_socket
    monkeypatch.setattr(viewer, "IDLE_SNAPSHOT_DELAY", 1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def resize(*args):
        entered.set()
        await release.wait()
        return socket.snapshot_bytes, 1280, 720

    monkeypatch.setattr(transport.asyncio, "to_thread", resize)
    async with viewer:
        capture = asyncio.create_task(viewer.capture_frame())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            socket.frame(2)
            await viewer.call("Input.insertText", {"text": "barrier"})
            release.set()
            await asyncio.wait_for(capture, 1)
            assert (await viewer.frames.get())["data"] == "2"
            assert viewer.metrics()["snapshots_dropped"] == 1
        finally:
            release.set()
            capture.cancel()
            await asyncio.gather(capture, return_exceptions=True)


@pytest.mark.asyncio
async def test_explicit_refresh_coalesces_pending_idle_snapshot(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        socket.frame(1)
        await viewer.call("Input.insertText", {"text": "barrier"})
        await viewer.capture_frame()
        await asyncio.sleep(0.2)
        assert sum(command["method"] == "Page.captureScreenshot" for command in socket.commands) == 1
        assert viewer.metrics()["source_frames"] == 1
        assert viewer.metrics()["source_frames_dropped"] == 1


@pytest.mark.asyncio
async def test_unchanged_screencast_feedback_does_not_discard_final_snapshot(viewer_socket, monkeypatch):
    viewer, socket = viewer_socket

    async def resize(*args):
        socket.frame(1)
        await viewer.call("Input.insertText", {"text": "barrier"})
        return socket.snapshot_bytes, 1280, 720

    monkeypatch.setattr(transport.asyncio, "to_thread", resize)
    async with viewer:
        socket.frame(1)
        await viewer.call("Input.insertText", {"text": "barrier"})
        await viewer.capture_frame()
        frame = await asyncio.wait_for(viewer.frames.get(), 1)
        assert frame["data"] == socket.snapshot_bytes and frame["priority"]
        await asyncio.sleep(0.2)
        assert viewer.metrics()["snapshots"] == 1
        assert viewer.metrics()["snapshots_dropped"] == 0
