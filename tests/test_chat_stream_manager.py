import asyncio

import pytest

from agent.modules.agent_runtime.chat_stream_manager import ChatStreamManager, ChatStreamSession


class FakeRateLimitError(Exception):
    status_code = 429


@pytest.mark.asyncio
@pytest.mark.parametrize("persist_fails", [False, True])
async def test_chat_stream_session_emits_classified_error(monkeypatch, persist_fails) -> None:
    recorded = []

    async def record_error(thread_id, event):
        recorded.append((thread_id, event))
        if persist_fails:
            raise OSError("Database unavailable")

    monkeypatch.setattr("agent.modules.conversations.record_thread_run_error", record_error)
    async def failing_run_fn(**_params):
        raise FakeRateLimitError("429 too many requests")
        yield  # pragma: no cover - makes this an async generator

    session = ChatStreamSession("thread-1", {}, failing_run_fn)
    session.start()

    error_event = None
    async for event in session.subscribe():
        if event.get("type") == "error":
            error_event = event
            break

    assert error_event is not None
    assert error_event["code"] == "rate_limit"
    assert "rate limiting" in error_event["content"].lower()
    assert recorded == [("thread-1", error_event)]

    if session.task is not None:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
    session.close()


@pytest.mark.asyncio
async def test_chat_stream_session_saves_custom_error_before_delivery(monkeypatch) -> None:
    recorded = []
    original_error = {
        "type": "error", "code": "tool_failure",
        "content": "Tool execution failed.\nCommand returned exit code 17.",
        "message": "The tool could not complete this request.",
    }

    async def record_error(thread_id, event):
        recorded.append((thread_id, event))

    async def run_fn(**params):
        yield original_error

    monkeypatch.setattr("agent.modules.conversations.record_thread_run_error", record_error)
    session = ChatStreamSession("custom-error", {}, run_fn)
    session.start()
    try:
        async for event in session.subscribe():
            assert event == original_error
            assert recorded == [("custom-error", event)]
            break
    finally:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
        session.close()


@pytest.mark.asyncio
async def test_superseded_stream_does_not_replace_saved_error(monkeypatch) -> None:
    async def record_error(thread_id, event):
        pytest.fail("Superseded streams must not overwrite the new run's error")

    monkeypatch.setattr("agent.modules.conversations.record_thread_run_error", record_error)
    session = ChatStreamSession("superseded-run", {})
    session.cancel(superseded=True)
    try:
        await session._push_error_event({"type": "error", "content": "Chat execution stopped."})
        session.done = True
        assert [event async for event in session.subscribe()] == [
            {"type": "error", "content": "Chat execution stopped.", "message": "Chat execution stopped."},
        ]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_chat_stream_session_emits_error_for_base_exception_group() -> None:
    async def failing_run_fn(**_params):
        raise BaseExceptionGroup(
            "llm failed",
            [asyncio.CancelledError(), FakeRateLimitError("429")],
        )
        yield  # pragma: no cover - makes this an async generator

    session = ChatStreamSession("thread-2", {}, failing_run_fn)
    session.start()

    error_event = None
    async for event in session.subscribe():
        if event.get("type") == "error":
            error_event = event
            break

    assert error_event is not None
    assert error_event["code"] == "rate_limit"

    if session.task is not None:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
    session.close()


@pytest.mark.asyncio
async def test_replay_keeps_all_events_and_final_after_spilling_to_disk(monkeypatch) -> None:
    monkeypatch.setattr("agent.modules.agent_runtime.chat_stream_manager.MAX_BUFFER_BYTES", 128)
    session = ChatStreamSession("long-stream", {})
    events = [{"type": "message", "content": "Unicode: \u65e5\u672c", "sequence": index} for index in range(10_002)]
    events.append({"type": "final", "content": "complete"})
    try:
        for event in events:
            await session.push_event(event)
        session.done = True
        assert [event async for event in session.subscribe()] == events
    finally:
        session.close()


@pytest.mark.asyncio
async def test_slow_and_fast_subscribers_keep_independent_cursors_and_bounded_wakeups(monkeypatch) -> None:
    monkeypatch.setattr("agent.modules.agent_runtime.chat_stream_manager.MAX_BUFFER_BYTES", 128)
    session = ChatStreamSession("slow-reader", {})
    first = {"type": "message", "content": "initial"}
    await session.push_event(first)
    slow = session.subscribe()
    fast = session.subscribe()
    try:
        assert await anext(slow) == first
        assert await anext(fast) == first
        expected = []
        for index in range(100):
            event = {"type": "message", "sequence": index}
            expected.append(event)
            await session.push_event(event)
            assert await anext(fast) == event
        assert all(queue.qsize() <= 1 and queue.maxsize == 1 for queue in session.queues)
        session.done = True
        assert [event async for event in slow] == expected
        assert [event async for event in fast] == []
    finally:
        await slow.aclose()
        await fast.aclose()
        session.close()


@pytest.mark.asyncio
async def test_waiting_subscriber_wakes_when_an_event_arrives() -> None:
    session = ChatStreamSession("waiting-reader", {})
    subscriber = session.subscribe()
    waiting = asyncio.create_task(anext(subscriber))
    try:
        await asyncio.sleep(0)
        event = {"type": "final", "content": "complete"}
        await session.push_event(event)
        assert await asyncio.wait_for(waiting, timeout=1) == event
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await subscriber.aclose()
        session.close()


@pytest.mark.asyncio
async def test_removing_a_session_releases_its_log_after_subscribers_finish() -> None:
    manager = ChatStreamManager()
    session = ChatStreamSession("closing-reader", {})
    manager._sessions[session.thread_id] = session
    await session.push_event({"type": "message", "content": "first"})
    await session.push_event({"type": "final", "content": "complete"})
    session.done = True
    subscriber = session.subscribe()
    assert (await anext(subscriber))["content"] == "first"
    await manager.remove_session(session.thread_id, session)
    assert not session._event_log.closed
    assert [event async for event in subscriber] == [{"type": "final", "content": "complete"}]
    assert session._event_log.closed
    assert [event async for event in session.subscribe()] == []
