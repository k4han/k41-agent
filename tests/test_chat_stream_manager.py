import asyncio
from contextlib import aclosing, asynccontextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agent.modules.agent_runtime.chat_stream_manager import ChatStreamManager, ChatStreamSession
from agent.modules.agent_runtime.protocol import application_event, protocol_event


def _wire_event(event):
    return protocol_event("custom:k41", {**event, "projection": "ui"})


async def _subscribe_updates(session):
    last_seq = 0
    async with aclosing(session.subscribe()) as events:
        async for event in events:
            assert event["type"] == "event"
            assert event["seq"] > last_seq
            assert event["event_id"] == f"{session.run_id}:{event['seq']}"
            last_seq = event["seq"]
            yield application_event(event)



@pytest_asyncio.fixture
async def stream_manager(monkeypatch):
    async def record_error(thread_id, event):
        pass

    monkeypatch.setattr("agent.modules.conversations.record_thread_run_error", record_error)
    manager = ChatStreamManager()
    try:
        yield manager
    finally:
        await manager.shutdown()


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
    async for event in _subscribe_updates(session):
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
        yield _wire_event(original_error)

    monkeypatch.setattr("agent.modules.conversations.record_thread_run_error", record_error)
    session = ChatStreamSession("custom-error", {}, run_fn)
    session.start()
    try:
        async for event in _subscribe_updates(session):
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
        assert [event async for event in _subscribe_updates(session)] == [
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
    async for event in _subscribe_updates(session):
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
            await session.push_event(_wire_event(event))
        session.done = True
        assert [event async for event in _subscribe_updates(session)] == events
    finally:
        session.close()


@pytest.mark.asyncio
async def test_slow_and_fast_subscribers_keep_independent_cursors_and_bounded_wakeups(monkeypatch) -> None:
    monkeypatch.setattr("agent.modules.agent_runtime.chat_stream_manager.MAX_BUFFER_BYTES", 128)
    session = ChatStreamSession("slow-reader", {})
    first = {"type": "message", "content": "initial"}
    await session.push_event(_wire_event(first))
    slow = _subscribe_updates(session)
    fast = _subscribe_updates(session)
    try:
        assert await anext(slow) == first
        assert await anext(fast) == first
        expected = []
        for index in range(100):
            event = {"type": "message", "sequence": index}
            expected.append(event)
            await session.push_event(_wire_event(event))
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
    subscriber = _subscribe_updates(session)
    waiting = asyncio.create_task(anext(subscriber))
    try:
        await asyncio.sleep(0)
        event = {"type": "final", "content": "complete"}
        await session.push_event(_wire_event(event))
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
    await session.push_event(_wire_event({"type": "message", "content": "first"}))
    await session.push_event(_wire_event({"type": "final", "content": "complete"}))
    session.done = True
    subscriber = _subscribe_updates(session)
    assert (await anext(subscriber))["content"] == "first"
    await manager.remove_session(session.thread_id, session)
    assert not session._event_log.closed
    assert [event async for event in subscriber] == [{"type": "final", "content": "complete"}]
    assert session._event_log.closed
    assert [event async for event in _subscribe_updates(session)] == []


@pytest.mark.asyncio
async def test_detached_execution_finishes_and_replays_without_subscribers(stream_manager):
    proceed = asyncio.Event()
    events = [{"type": "message", "content": "first"}, {"type": "final", "content": "complete"}]

    async def run(**params):
        yield _wire_event(events[0])
        await proceed.wait()
        yield _wire_event(events[1])

    session = await stream_manager.get_or_create_session("detached", {}, run)
    subscriber = _subscribe_updates(session)
    assert await anext(subscriber) == events[0]
    await subscriber.aclose()
    assert not session.task.done()
    proceed.set()
    await asyncio.wait_for(session.task, timeout=1)
    assert [event async for event in _subscribe_updates(session)] == events


@pytest.mark.asyncio
async def test_replacement_waits_for_cleanup_without_blocking_other_threads(stream_manager):
    old_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    new_started = asyncio.Event()
    calls = []

    async def old_run(**params):
        old_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            calls.append("old-cleaned")
        yield  # pragma: no cover

    async def new_run(**params):
        calls.append("new-started")
        new_started.set()
        yield _wire_event({"type": "final", "content": "complete"})

    old = await stream_manager.get_or_create_session("shared", {}, old_run)
    await old_started.wait()
    replacement = asyncio.create_task(stream_manager.get_or_create_session("shared", {}, new_run))
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(new_started.wait(), timeout=0.1)
        assert await stream_manager.get_session("shared") is old

        unrelated = await asyncio.wait_for(
            stream_manager.get_or_create_session("unrelated", {}, new_run), timeout=1,
        )
        await asyncio.wait_for(unrelated.task, timeout=1)
        calls.clear()
    finally:
        release_cleanup.set()
        new = await asyncio.wait_for(replacement, timeout=1)
        await asyncio.wait_for(new.task, timeout=1)

    assert calls == ["old-cleaned", "new-started"]
    assert old.task.done()
    assert old._event_log.closed
    assert await stream_manager.get_session("shared") is new


@pytest.mark.asyncio
async def test_cancelling_replacement_request_does_not_cancel_old_cleanup(stream_manager):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned = asyncio.Event()

    async def run(**params):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleaned.set()
        yield  # pragma: no cover

    old = await stream_manager.get_or_create_session("cancel-request", {}, run)
    await started.wait()
    replacement = asyncio.create_task(stream_manager.get_or_create_session(old.thread_id, {}, run))
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        replacement.cancel()
        await asyncio.gather(replacement, return_exceptions=True)
        old.cancel()
        await asyncio.sleep(0)
        assert not old.task.done()
        assert not cleaned.is_set()
        assert await stream_manager.get_session(old.thread_id) is old
    finally:
        release_cleanup.set()
        await asyncio.gather(old.task, return_exceptions=True)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_repeated_dashboard_stop_cancels_a_run_that_swallowed_cancellation(stream_manager, monkeypatch):
    from agent.delivery.http.dashboard.routes import dashboard

    started = asyncio.Event()
    swallowed = asyncio.Event()
    release = asyncio.Event()

    async def run(**params):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            swallowed.set()
            await release.wait()
        yield _wire_event({"type": "final"})

    monkeypatch.setattr(dashboard, "get_chat_stream_manager", lambda: stream_manager)
    monkeypatch.setattr(dashboard, "peek_acp_service", lambda: None)
    monkeypatch.setattr(
        dashboard, "get_active_session_registry",
        lambda: SimpleNamespace(cancel_by_thread=lambda thread_id: False),
    )
    session = await stream_manager.get_or_create_session("repeated-stop", {}, run)
    await started.wait()
    try:
        assert await dashboard.stop_dashboard_session({"thread_id": session.thread_id}) == {"success": True}
        await asyncio.wait_for(swallowed.wait(), timeout=1)
        assert session.task.cancelling() == 1
        assert await dashboard.stop_dashboard_session({"thread_id": session.thread_id}) == {"success": True}
        _, pending = await asyncio.wait({session.task}, timeout=0.1)
        assert not pending
        assert session.task.cancelled()
    finally:
        release.set()
        await asyncio.gather(session.task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replacement", "shutdown"])
@pytest.mark.parametrize("already_cancelled", [False, True])
async def test_stream_stop_retries_swallowed_cancellation(stream_manager, monkeypatch, operation, already_cancelled):
    monkeypatch.setattr(
        "agent.modules.agent_runtime.chat_stream_manager.CANCELLATION_GRACE_PERIOD_SECONDS", 0.02,
    )
    started = asyncio.Event()
    swallowed = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def old_run(**params):
        started.set()
        try:
            try:
                await release.wait()
            except asyncio.CancelledError:
                swallowed.set()
                await release.wait()
        finally:
            calls.append("old-cleaned")
        yield _wire_event({"type": "final"})

    async def new_run(**params):
        calls.append("new-started")
        yield _wire_event({"type": "final"})

    old = await stream_manager.get_or_create_session("swallowed-cancel", {}, old_run)
    await started.wait()
    if already_cancelled:
        old.cancel()
        await asyncio.wait_for(swallowed.wait(), timeout=1)
    stop = asyncio.create_task(
        stream_manager.get_or_create_session(old.thread_id, {}, new_run)
        if operation == "replacement" else stream_manager.shutdown()
    )
    try:
        _, pending = await asyncio.wait({stop}, timeout=0.5)
        assert not pending
        result = await stop
        assert swallowed.is_set()
        assert old.task.cancelled()
        assert old._event_log.closed
        if operation == "replacement":
            await asyncio.wait_for(result.task, timeout=1)
            assert calls == ["old-cleaned", "new-started"]
        else:
            assert calls == ["old-cleaned"]
            assert await stream_manager.get_session(old.thread_id) is None
    finally:
        release.set()
        await asyncio.gather(stop, old.task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replacement", "shutdown"])
async def test_stream_stop_times_out_without_forgetting_a_running_task(stream_manager, monkeypatch, operation):
    monkeypatch.setattr(
        "agent.modules.agent_runtime.chat_stream_manager.CANCELLATION_GRACE_PERIOD_SECONDS", 0.02,
    )
    started = asyncio.Event()
    release = asyncio.Event()
    new_started = False

    async def old_run(**params):
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        yield _wire_event({"type": "final"})

    async def new_run(**params):
        nonlocal new_started
        new_started = True
        yield _wire_event({"type": "final"})

    old = await stream_manager.get_or_create_session("ignores-cancel", {}, old_run)
    await started.wait()
    stop = asyncio.create_task(
        stream_manager.get_or_create_session(old.thread_id, {}, new_run)
        if operation == "replacement" else stream_manager.shutdown()
    )
    try:
        _, pending = await asyncio.wait({stop}, timeout=0.5)
        assert not pending
        with pytest.raises(TimeoutError, match="ignores-cancel"):
            await stop
        assert not new_started
        assert not old.task.done()
        assert not old._event_log.closed
        assert await stream_manager.get_session(old.thread_id) is old
        assert not stream_manager._thread_locks
    finally:
        release.set()
        await asyncio.gather(stop, old.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_queued_replacements_never_execute_together(stream_manager):
    started = [asyncio.Event() for _ in range(3)]
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    active = 0
    peak_active = 0
    cleaned = []

    async def run(turn, **params):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        started[turn].set()
        try:
            await asyncio.Event().wait()
        finally:
            if turn == 0:
                cleanup_started.set()
                await release_cleanup.wait()
            await asyncio.sleep(0)
            cleaned.append(turn)
            active -= 1
        yield  # pragma: no cover

    first = await stream_manager.get_or_create_session("queued", {"turn": 0}, run)
    await started[0].wait()
    second_request = asyncio.create_task(stream_manager.get_or_create_session("queued", {"turn": 1}, run))
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        third_request = asyncio.create_task(stream_manager.get_or_create_session("queued", {"turn": 2}, run))
        await asyncio.sleep(0)
    finally:
        release_cleanup.set()
    second, third = await asyncio.wait_for(asyncio.gather(second_request, third_request), timeout=1)
    await asyncio.wait_for(started[2].wait(), timeout=1)
    assert first.task.done()
    assert second.task.done()
    assert await stream_manager.get_session("queued") is third
    assert peak_active == 1
    assert cleaned == [0, 1]


@pytest.mark.asyncio
async def test_shutdown_prevents_a_waiting_replacement_from_starting(stream_manager):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    calls = []

    async def run(turn, **params):
        calls.append(turn)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
        yield  # pragma: no cover

    old = await stream_manager.get_or_create_session("closing-replacement", {"turn": "old"}, run)
    await started.wait()
    replacement = asyncio.create_task(stream_manager.get_or_create_session(old.thread_id, {"turn": "new"}, run))
    shutdown = None
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        shutdown = asyncio.create_task(stream_manager.shutdown())
        await asyncio.sleep(0)
    finally:
        release_cleanup.set()
    with pytest.raises(RuntimeError, match="shutting down"):
        await asyncio.wait_for(replacement, timeout=1)
    await asyncio.wait_for(shutdown, timeout=1)
    assert calls == ["old"]
    assert old.task.done()
    assert old._event_log.closed
    assert await stream_manager.get_session(old.thread_id) is None


@pytest.mark.asyncio
async def test_cancel_before_task_start_ends_subscriptions_and_releases_log(stream_manager, monkeypatch):
    monkeypatch.setattr("agent.modules.agent_runtime.chat_stream_manager.SESSION_GRACE_PERIOD_SECONDS", 0)
    called = False

    async def run(**params):
        nonlocal called
        called = True
        yield _wire_event({"type": "final"})

    session = await stream_manager.get_or_create_session("cancel-before-start", {}, run)
    session.cancel()
    await asyncio.gather(session.task, return_exceptions=True)

    async def read_events():
        return [event async for event in _subscribe_updates(session)]

    assert await asyncio.wait_for(read_events(), timeout=1) == []
    await asyncio.sleep(0)
    assert not called
    assert session.done
    assert await stream_manager.get_session(session.thread_id) is None
    assert session._event_log.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_after_completion", [False, True])
async def test_completed_run_keeps_replay_until_expiry(stream_manager, monkeypatch, cancel_after_completion):
    monkeypatch.setattr("agent.modules.agent_runtime.chat_stream_manager.SESSION_GRACE_PERIOD_SECONDS", 0.05)
    event = {"type": "final", "content": "complete"}

    async def run(**params):
        yield _wire_event(event)

    session = await stream_manager.get_or_create_session("retained", {}, run)
    await asyncio.wait_for(session.task, timeout=1)
    if cancel_after_completion:
        session.cancel()
    assert await stream_manager.get_session(session.thread_id) is session
    assert [item async for item in _subscribe_updates(session)] == [event]

    await asyncio.sleep(0.1)
    assert await stream_manager.get_session(session.thread_id) is None
    assert session._event_log.closed


@pytest.mark.asyncio
async def test_replacing_completed_run_cannot_expire_its_successor(stream_manager, monkeypatch):
    monkeypatch.setattr("agent.modules.agent_runtime.chat_stream_manager.SESSION_GRACE_PERIOD_SECONDS", 0.05)
    started = asyncio.Event()

    async def completed_run(**params):
        yield _wire_event({"type": "final"})

    async def running(**params):
        started.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover

    old = await stream_manager.get_or_create_session("successor", {}, completed_run)
    await old.task
    new = await stream_manager.get_or_create_session(old.thread_id, {}, running)
    await started.wait()
    await asyncio.sleep(0.1)
    assert await stream_manager.get_session(old.thread_id) is new
    assert not new.task.done()
    assert old._event_log.closed


@pytest.mark.asyncio
async def test_cancel_during_event_delivery_closes_run_in_its_original_context(stream_manager, monkeypatch):
    from agent.modules.agent_runtime import workspace_admission

    scope = ContextVar("test_stream_scope", default=None)
    delivering = asyncio.Event()
    calls = []

    @asynccontextmanager
    async def workspace(arguments):
        try:
            yield
        finally:
            calls.append("workspace-closed")

    @workspace_admission.guard_workspace_run
    async def run(**params):
        token = scope.set("running")
        try:
            yield _wire_event({"type": "message", "content": "first"})
        finally:
            scope.reset(token)
            calls.append("run-closed")

    monkeypatch.setattr(workspace_admission, "workspace_run", workspace)
    session = await stream_manager.get_or_create_session("cancel-delivery", {}, run)
    original_push = session.push_event

    async def push(event):
        if application_event(event).get("type") == "message":
            delivering.set()
            await asyncio.Event().wait()
        await original_push(event)

    monkeypatch.setattr(session, "push_event", push)
    await asyncio.wait_for(delivering.wait(), timeout=1)
    session.cancel()
    await asyncio.wait_for(asyncio.gather(session.task, return_exceptions=True), timeout=1)
    assert calls == ["run-closed", "workspace-closed"]
    assert scope.get() is None


@pytest.mark.asyncio
async def test_shutdown_rejects_new_runs_and_waits_for_cleanup(stream_manager):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def run(**params):
        started.set()
        try:
            yield _wire_event({"type": "message", "content": "first"})
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    session = await stream_manager.get_or_create_session("shutdown", {}, run)
    subscriber = _subscribe_updates(session)
    await anext(subscriber)
    shutdown = asyncio.create_task(stream_manager.shutdown())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        assert not shutdown.done()
        with pytest.raises(RuntimeError, match="shutting down"):
            await stream_manager.get_or_create_session("rejected", {}, run)
    finally:
        release_cleanup.set()
        await asyncio.wait_for(shutdown, timeout=1)

    assert session.task.done()
    assert await stream_manager.get_session(session.thread_id) is None
    assert not session._event_log.closed
    assert [event async for event in subscriber] == [
        {"type": "error", "content": "Chat execution stopped.", "message": "Chat execution stopped."},
    ]
    assert session._event_log.closed
    await stream_manager.shutdown()
