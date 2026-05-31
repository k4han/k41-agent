import asyncio

import pytest

from agent.modules.agent_runtime.chat_stream_manager import ChatStreamSession


class FakeRateLimitError(Exception):
    status_code = 429


@pytest.mark.asyncio
async def test_chat_stream_session_emits_classified_error() -> None:
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

    if session.task is not None:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)


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
