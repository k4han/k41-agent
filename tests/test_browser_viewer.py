"""CDP stream lifecycle and source pacing regression checks."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from agent.modules.browser import driver as transport


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.acks = asyncio.Queue()
        self.commands = []

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
        result = {"data": "final"} if message["method"] == "Page.captureScreenshot" else {}
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
async def test_frame_ack_pacing_keeps_commands_responsive_and_frames_latest(viewer_socket):
    viewer, socket = viewer_socket
    viewer.FRAME_INTERVAL = 0.2
    async with viewer:
        socket.frame(1)
        socket.frame(2)
        await asyncio.wait_for(viewer.call("Input.insertText", {"text": "hello"}), 0.1)
        assert socket.acks.empty()
        assert (await viewer.frames.get())["data"] == "2"
        first, second = [await asyncio.wait_for(socket.acks.get(), 1) for _ in range(2)]
        assert [first[0], second[0]] == [1, 2]
        assert second[1] - first[1] >= viewer.FRAME_INTERVAL * 0.9
    assert viewer.reader.done()
    assert viewer.acknowledger.done()


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
        await asyncio.wait_for(viewer.wait_closed(), 1)
    assert isinstance(viewer.acknowledger.exception(), ConnectionError)


@pytest.mark.asyncio
async def test_idle_source_flushes_final_paint_once_without_restarting_static_capture(viewer_socket):
    viewer, socket = viewer_socket
    async with viewer:
        socket.frame(1)
        assert (await asyncio.wait_for(viewer.frames.get(), 1))["data"] == "1"
        assert (await asyncio.wait_for(viewer.frames.get(), 1))["data"] == "final"
        # A screenshot can itself cause a screencast event. Its unchanged image
        # must not schedule another trailing screenshot, even if the screenshot
        # encoder produced different JPEG bytes.
        socket.incoming.put_nowait({"method": "Page.screencastFrame", "params": {
            "sessionId": 2, "data": "1", "metadata": {},
        }})
        await asyncio.wait_for(socket.acks.get(), 1)
        await asyncio.wait_for(socket.acks.get(), 1)
        await asyncio.sleep(0.25)
        assert sum(command["method"] == "Page.captureScreenshot" for command in socket.commands) == 1
