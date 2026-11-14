import asyncio
import importlib
import json
from contextlib import aclosing

import anyio
import pytest
from starlette.requests import ClientDisconnect, Request

from agent.delivery.http.api.schemas import ChatRequest
from agent.delivery.http.common import streaming
from agent.modules.agent_runtime.chat_stream_manager import ChatStreamManager
from agent.modules.agent_runtime.protocol import protocol_event


api = importlib.import_module("agent.delivery.http.api.router")
events_api = importlib.import_module("agent.delivery.http.api.events_v2")


def connected_request():
    return Request({"type": "http"}, asyncio.Queue().get)


@pytest.mark.asyncio
async def test_idle_ndjson_heartbeats_preserve_slow_run_and_replay(monkeypatch):
    monkeypatch.setattr(streaming, "STREAM_HEARTBEAT_SECONDS", 0.01)
    manager = ChatStreamManager()
    proceed = asyncio.Event()
    starts = []

    async def run(**params):
        starts.append(params)
        await proceed.wait()
        yield protocol_event("custom:k41", {"type": "final", "content": "done", "projection": "ui"})

    session = await manager.get_or_create_session("heartbeat", {}, run_fn=run)
    response = events_api.stream_response(connected_request(), session)
    try:
        async with aclosing(response.body_iterator) as chunks:
            for _ in range(3):
                assert await asyncio.wait_for(anext(chunks), 1) == "\n"
            assert len(starts) == 1
            assert session.last_seq == 0
            assert not session.task.done()
            proceed.set()
            event = json.loads(await asyncio.wait_for(anext(chunks), 1))
            assert event["seq"] == 1
            assert [chunk async for chunk in chunks] == []
        assert not session.queues
        replay = events_api.stream_response(connected_request(), session)
        assert [json.loads(chunk) async for chunk in replay.body_iterator] == [event]
    finally:
        proceed.set()
        await response.body_iterator.aclose()
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("background", [False, True])
async def test_disconnect_closes_silent_stream(monkeypatch, spec_version, background):
    monkeypatch.setattr(streaming, "DISCONNECT_POLL_SECONDS", 0.01)
    started = asyncio.Event()
    closed = asyncio.Event()
    disconnect = asyncio.Event()
    manager = ChatStreamManager()

    async def prepare(*args, **kwargs):
        return {}

    async def run(**params):
        try:
            started.set()
            await asyncio.Event().wait()
            yield protocol_event("custom:k41", {"type": "final"}) if background else "unreachable"
        finally:
            closed.set()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            assert not message.get("body")

    monkeypatch.setattr(api, "prepare_run_params", prepare)
    monkeypatch.setattr(api, "run_agent", run)
    scope = {"type": "http", "asgi": {"spec_version": spec_version}}
    request = Request(scope, receive)
    if background:
        session = await manager.get_or_create_session("silent-disconnect", {}, run_fn=run)
        response = events_api.stream_response(request, session)
    else:
        response = await api.chat_stream(ChatRequest(message="hello"), request, admin="test")
    task = asyncio.create_task(response(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), 1)
        disconnect.set()
        await asyncio.wait_for(task, 1)
        if background:
            assert not session.queues
            assert not session.task.done()
            assert not closed.is_set()
        else:
            assert closed.is_set()
    finally:
        disconnect.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_send_failure_closes_transport_iterator(monkeypatch, background):
    started = asyncio.Event()
    closed = asyncio.Event()
    proceed = asyncio.Event()
    manager = ChatStreamManager()

    async def run(**params):
        try:
            started.set()
            yield protocol_event("custom:k41", {"type": "message", "content": "first"}) if background else "first"
            await proceed.wait()
        finally:
            closed.set()

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("Client disconnected")

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
    request = Request(scope, receive)
    if background:
        session = await manager.get_or_create_session("send-failure", {}, run_fn=run)
        response = events_api.stream_response(request, session)
    else:
        async def prepare(*args, **kwargs):
            return {}

        monkeypatch.setattr(api, "prepare_run_params", prepare)
        monkeypatch.setattr(api, "run_agent", run)
        response = await api.chat_stream(ChatRequest(message="hello"), request, admin="test")
    try:
        with pytest.raises(ClientDisconnect):
            await asyncio.wait_for(response(scope, receive, send), 1)
        assert started.is_set()
        if background:
            assert not session.queues
            assert not session.task.done()
            assert not closed.is_set()
        else:
            assert closed.is_set()
    finally:
        proceed.set()
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_producer_keeps_cancel_scope_in_one_task(monkeypatch, cancel):
    monkeypatch.setattr(streaming, "STREAM_HEARTBEAT_SECONDS", 0.01)
    proceed = asyncio.Event()

    async def source():
        with anyio.CancelScope():
            yield "first"
            await proceed.wait()
            yield "last"

    async with aclosing(streaming.stream_chat_chunks(source(), connected_request(), heartbeat="\n")) as chunks:
        assert await anext(chunks) == "first"
        assert await asyncio.wait_for(anext(chunks), 1) == "\n"
        if cancel:
            return
        proceed.set()
        assert await asyncio.wait_for(anext(chunks), 1) == "last"
        assert [chunk async for chunk in chunks] == []


@pytest.mark.asyncio
async def test_burst_stream_preserves_order_without_per_chunk_disconnect_polls(monkeypatch):
    monkeypatch.setattr(streaming, "DISCONNECT_POLL_SECONDS", 60)
    request = connected_request()
    is_disconnected = request.is_disconnected
    polls = 0

    async def count_polls():
        nonlocal polls
        polls += 1
        return await is_disconnected()

    monkeypatch.setattr(request, "is_disconnected", count_polls)
    expected = [str(index) for index in range(256)]

    async def source():
        for chunk in expected:
            yield chunk

    assert [chunk async for chunk in streaming.stream_chat_chunks(source(), request)] == expected
    assert polls == 1


@pytest.mark.asyncio
async def test_burst_stream_keeps_bounded_read_ahead_and_closes():
    produced = 0
    closed = asyncio.Event()

    async def source():
        nonlocal produced
        try:
            for index in range(10000):
                produced += 1
                yield str(index)
        finally:
            closed.set()

    async with aclosing(streaming.stream_chat_chunks(source(), connected_request())) as chunks:
        assert await anext(chunks) == "0"
        await asyncio.sleep(0)
        assert produced < 10000
        assert not closed.is_set()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_disconnect_closes_busy_stream(monkeypatch):
    monkeypatch.setattr(streaming, "DISCONNECT_POLL_SECONDS", 0.01)
    disconnect = asyncio.Event()
    closed = asyncio.Event()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def source():
        try:
            while True:
                yield "chunk"
        finally:
            closed.set()

    async def drain(chunks):
        async for chunk in chunks:
            assert chunk == "chunk"
            await asyncio.sleep(0)

    request = Request({"type": "http"}, receive)
    async with aclosing(streaming.stream_chat_chunks(source(), request)) as chunks:
        assert await anext(chunks) == "chunk"
        disconnect.set()
        await asyncio.wait_for(drain(chunks), 1)
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_count", [1, 256])
async def test_producer_error_is_forwarded_and_closed(chunk_count):
    closed = asyncio.Event()

    async def source():
        try:
            for index in range(chunk_count):
                yield str(index)
            raise ValueError("broken stream")
        finally:
            closed.set()

    async with aclosing(streaming.stream_chat_chunks(source(), connected_request())) as chunks:
        for index in range(chunk_count):
            assert await anext(chunks) == str(index)
        with pytest.raises(ValueError, match="broken stream"):
            await asyncio.wait_for(anext(chunks), 1)
    assert closed.is_set()
