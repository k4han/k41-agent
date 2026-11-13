"""Binary downloads must use bounded provider reads and close on disconnect."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from starlette.requests import ClientDisconnect

from agent.delivery.http.common import LegacyAPIRedirectMiddleware
from agent.delivery.http.dashboard.routes.workspace import _workspace_file_response
from agent.modules.instabox.client import InstaboxClient
from agent.modules.workspaces import WorkspaceRef
from agent.modules.workspaces.daytona_backend import DaytonaWorkspaceBackend
from agent.modules.workspaces.local_backend import LocalWorkspaceBackend
from agent.modules.workspaces.modal_backend import ModalWorkspaceBackend
from agent.modules.workspaces.reads import WorkspaceByteStream
from agent.shared.infrastructure.csrf_protection import CSRFProtectionMiddleware
from agent.shared.infrastructure.http_logging import HTTPLoggingMiddleware

CHUNK_SIZE = 64 * 1024
DATA = b"\x00\xff\x80binary" * 30_000


class AsyncChunks:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False
        self.reads = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = next(self.chunks, None)
        if chunk is None:
            raise StopAsyncIteration
        self.reads += 1
        return chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_daytona_stream_uses_sdk_iterator_off_event_loop():
    main_thread = threading.get_ident()
    provider_threads = []
    closed = []

    def download(path):
        assert path == "/workspace/image.png"
        try:
            for offset in range(0, len(DATA), 100_000):
                provider_threads.append(threading.get_ident())
                yield DATA[offset:offset + 100_000]
        finally:
            closed.append(True)

    backend = DaytonaWorkspaceBackend.__new__(DaytonaWorkspaceBackend)
    backend.root = "/workspace"
    backend.ensure_active = Mock()
    backend.fs = SimpleNamespace(download_file_stream=download)
    backend.read_bytes = AsyncMock(side_effect=AssertionError("Whole-file reads are forbidden"))
    chunks = [chunk async for chunk in backend.stream_bytes("image.png")]
    assert b"".join(chunks) == DATA
    assert max(map(len, chunks)) <= CHUNK_SIZE
    assert all(thread != main_thread for thread in provider_threads)
    assert closed
    backend.read_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_stream_preserves_binary_and_closes_sdk_readers():
    stdout = AsyncChunks([DATA])
    stderr = AsyncChunks([])
    process = SimpleNamespace(stdout=stdout, stderr=stderr, wait=SimpleNamespace(aio=AsyncMock(return_value=0)))
    execute = AsyncMock(return_value=process)
    fs = SimpleNamespace(stat=Mock(aio=AsyncMock(return_value=SimpleNamespace(is_dir=False))))
    backend = ModalWorkspaceBackend(WorkspaceRef(backend="modal", locator="test", metadata={"root": "/workspace"}),
                                    sandbox=SimpleNamespace(exec=SimpleNamespace(aio=execute)), fs=fs)
    backend.read_bytes = AsyncMock(side_effect=AssertionError("Whole-file reads are forbidden"))
    chunks = [chunk async for chunk in backend.stream_bytes("image.png")]
    assert b"".join(chunks) == DATA
    assert max(map(len, chunks)) <= CHUNK_SIZE
    execute.assert_awaited_once_with("cat", "--", "/workspace/image.png", text=False, timeout=300)
    assert stdout.closed and stderr.closed
    backend.read_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_saved_stream_reads_file_in_bounded_chunks(tmp_path):
    (tmp_path / "file.bin").write_bytes(DATA)
    backend = LocalWorkspaceBackend(WorkspaceRef(locator=str(tmp_path)))
    chunks = [chunk async for chunk in backend.stream_bytes("file.bin")]
    assert b"".join(chunks) == DATA
    assert max(map(len, chunks)) <= CHUNK_SIZE


@pytest.mark.asyncio
async def test_instabox_stream_is_lazy_and_closes_http_response():
    source = AsyncChunks([DATA[:CHUNK_SIZE], DATA[CHUNK_SIZE:]])

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            async for chunk in source:
                yield chunk

        async def aclose(self):
            await source.aclose()

    def handler(request):
        assert request.headers["X-API-Key"] == "test-key"
        assert request.url.params["path"] == "/workspace/file.bin"
        return httpx.Response(200, stream=Body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        api = InstaboxClient(api_key="test-key", client=client)
        stream = await WorkspaceByteStream.open(api.stream_file("machine", "/workspace/file.bin"))
        assert source.reads == 1
        assert await anext(stream) == DATA[:CHUNK_SIZE]
        assert source.reads == 1
        await stream.aclose()
        assert source.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["http.response.start", "http.response.body"])
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_download_closes_prefetched_reader_on_send_failure(monkeypatch, fail_at, failure):
    import agent.modules.workspaces as workspaces

    source = AsyncChunks([b"first", b"second"])
    reader = SimpleNamespace(stream_bytes=Mock(return_value=source))
    monkeypatch.setattr(workspaces, "get_workspace_file_io", AsyncMock(return_value=reader))
    workspace = WorkspaceRef(backend="modal", locator="test", metadata={"root": "/workspace"})
    response = await _workspace_file_response(workspace, None, "file.bin", inline=False)
    assert source.reads == 1

    async def receive():
        raise AssertionError("No receive is needed for ASGI 2.4 streaming")

    async def send(message):
        if message["type"] == fail_at:
            raise failure()

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET", "path": "/download",
             "scheme": "https", "server": ("testserver", 443), "query_string": b"", "headers": []}
    wrapped = HTTPLoggingMiddleware(CSRFProtectionMiddleware(LegacyAPIRedirectMiddleware(response)))
    with pytest.raises(ClientDisconnect if failure is OSError else asyncio.CancelledError):
        await wrapped(scope, receive, send)
    assert source.closed
    assert source.reads == 1


@pytest.mark.asyncio
async def test_http_disconnect_cancels_blocked_download_and_closes_source(monkeypatch):
    import agent.modules.workspaces as workspaces

    disconnect = asyncio.Event()
    closed = []

    async def chunks(path):
        try:
            yield b"first"
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    monkeypatch.setattr(workspaces, "get_workspace_file_io", AsyncMock(return_value=SimpleNamespace(stream_bytes=chunks)))
    workspace = WorkspaceRef(backend="modal", locator="test", metadata={"root": "/workspace"})
    response = await _workspace_file_response(workspace, None, "file.bin", inline=False)

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            assert message["body"] == b"first"
            disconnect.set()

    scope = {"type": "http", "asgi": {"spec_version": "2.3"}, "method": "GET", "path": "/download",
             "scheme": "http", "server": ("testserver", 80), "query_string": b"", "headers": []}
    wrapped = HTTPLoggingMiddleware(CSRFProtectionMiddleware(LegacyAPIRedirectMiddleware(response)))
    await asyncio.wait_for(wrapped(scope, receive, send), timeout=1)
    assert closed
