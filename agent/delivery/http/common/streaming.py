"""Keep chat transports responsive and close their iterators on disconnect."""

import asyncio
from collections.abc import AsyncGenerator

import anyio
from fastapi import Request
from fastapi.responses import StreamingResponse


STREAM_HEARTBEAT_SECONDS = 15.0
DISCONNECT_POLL_SECONDS = 1.0
STREAM_BUFFER_CHUNKS = 32


class ChatStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ASGI 2.4 can fail in send() while the iterator is suspended at yield.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


async def stream_chat_chunks(
    chunks: AsyncGenerator[str, None],
    request: Request,
    *,
    heartbeat: str | None = None,
) -> AsyncGenerator[str, None]:
    """Poll disconnects without cancelling a slow producer at each heartbeat."""
    # Absorb small bursts while retaining backpressure for slow clients.
    queue: asyncio.Queue[str | Exception | None] = asyncio.Queue(maxsize=STREAM_BUFFER_CHUNKS)

    async def produce() -> None:
        try:
            async for chunk in chunks:
                await queue.put(chunk)
        except Exception as exc:
            await queue.put(exc)
        else:
            await queue.put(None)
        finally:
            with anyio.CancelScope(shield=True):
                await chunks.aclose()

    # One task owns every iteration, including any task-bound cancel scopes
    # opened by tools inside the agent generator.
    producer = asyncio.create_task(produce())
    loop = asyncio.get_running_loop()
    last_sent = loop.time()
    next_disconnect_check = last_sent
    try:
        while True:
            if loop.time() >= next_disconnect_check:
                if await request.is_disconnected():
                    return
                next_disconnect_check = loop.time() + DISCONNECT_POLL_SECONDS
            try:
                chunk = queue.get_nowait()
            except asyncio.QueueEmpty:
                timeout = max(0.001, next_disconnect_check - loop.time())
                if heartbeat is not None:
                    timeout = min(timeout, max(0.001, STREAM_HEARTBEAT_SECONDS - (loop.time() - last_sent)))
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    if heartbeat is not None and loop.time() - last_sent >= STREAM_HEARTBEAT_SECONDS:
                        yield heartbeat
                        last_sent = loop.time()
                    continue
            if chunk is None:
                return
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk
            last_sent = loop.time()
    finally:
        # Starlette's disconnect listener cancels an AnyIO scope. Shield cleanup
        # so the pending producer and its nested generators finish closing.
        with anyio.CancelScope(shield=True):
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            await chunks.aclose()
