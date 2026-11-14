import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from agent.modules.tools.builtin.web import web_fetch as fetch_module
from agent.modules.tools.result import ToolError, ToolErrorCode


@pytest.fixture
def mock_http(monkeypatch):
    """Exercise the shared client with real HTTPX request handling."""
    client_type = httpx.Client
    clients = []
    monkeypatch.setattr(fetch_module, "_http_client", None)

    def install(handler):
        def create_client(**kwargs):
            client = client_type(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(fetch_module.httpx, "Client", create_client)
        return clients

    yield install
    fetch_module._close_http_client()


def test_client_reuses_pool_and_isolates_request_credentials(mock_http):
    requests = []

    def handle(request):
        requests.append(request)
        headers = {"set-cookie": "session=private; Path=/"}
        if request.url == fetch_module.TAVILY_EXTRACT_URL:
            return httpx.Response(200, json={"results": [{"raw_content": "tavily"}]}, headers=headers)
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"markdown": "firecrawl"}}, headers=headers)
        return httpx.Response(200, text="local", headers=headers)

    clients = mock_http(handle)
    base_url = "https://api.firecrawl.dev/v2"
    assert fetch_module._firecrawl_fetch(base_url, api_key="first-key") == "firecrawl"
    assert fetch_module._firecrawl_fetch(base_url, api_key="updated-key") == "firecrawl"
    assert fetch_module._tavily_fetch(base_url, api_key="tavily-key") == "tavily"
    assert fetch_module._local_fetch(base_url) == "local"
    assert len(clients) == 1
    assert not clients[0].is_closed
    assert [request.headers.get("authorization") for request in requests] == [
        "Bearer first-key", "Bearer updated-key", "Bearer tavily-key", None,
    ]
    assert all("cookie" not in request.headers for request in requests)

    fetch_module._close_http_client()
    assert clients[0].is_closed
    assert fetch_module._http_client is None


def test_client_initialization_is_thread_safe(mock_http):
    clients = mock_http(lambda request: httpx.Response(200))
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: fetch_module._get_http_client(), range(32)))
    assert len(clients) == 1
    assert all(client is clients[0] for client in results)


def test_auto_fallback_uses_remaining_timeout(monkeypatch, mock_http):
    now = [100.0]
    timeouts = []
    monkeypatch.setattr(fetch_module, "monotonic", lambda: now[0])

    def handle(request):
        timeouts.append(request.extensions["timeout"])
        if request.url == fetch_module.FIRECRAWL_SCRAPE_URL:
            now[0] += 12
            raise httpx.ReadTimeout("firecrawl timed out", request=request)
        if request.url == fetch_module.TAVILY_EXTRACT_URL:
            now[0] += 11
            raise httpx.ReadTimeout("tavily timed out", request=request)
        return httpx.Response(200, text="local fallback")

    mock_http(handle)
    tool = fetch_module._build_web_fetch_tool({
        "provider": "auto", "firecrawl_api_key": "fc-key", "tavily_api_key": "tv-key",
    })
    assert tool.func("https://example.test") == "local fallback"
    assert [timeout["read"] for timeout in timeouts] == [30.0, 18.0, 7.0]
    assert [timeout["connect"] for timeout in timeouts] == [10.0, 10.0, 7.0]
    assert [timeout["write"] for timeout in timeouts] == [30.0, 18.0, 7.0]
    assert [timeout["pool"] for timeout in timeouts] == [30.0, 18.0, 7.0]


@pytest.mark.parametrize("exhausted_provider", ["firecrawl", "tavily"])
def test_auto_stops_fallback_when_deadline_expires(monkeypatch, mock_http, exhausted_provider):
    now = [100.0]
    requests = []
    monkeypatch.setattr(fetch_module, "monotonic", lambda: now[0])

    def handle(request):
        requests.append(str(request.url))
        if exhausted_provider == "firecrawl" or request.url == fetch_module.TAVILY_EXTRACT_URL:
            now[0] = 130.0
        else:
            now[0] += 5
        raise httpx.ReadTimeout("provider timed out", request=request)

    mock_http(handle)
    tool = fetch_module._build_web_fetch_tool({
        "provider": "auto", "firecrawl_api_key": "fc-key", "tavily_api_key": "tv-key",
    })
    with pytest.raises(ToolError) as error:
        tool.func("https://example.test")
    assert error.value.code == ToolErrorCode.TIMEOUT
    expected = [fetch_module.FIRECRAWL_SCRAPE_URL]
    if exhausted_provider == "tavily":
        expected.append(fetch_module.TAVILY_EXTRACT_URL)
    assert requests == expected


def test_local_fetch_checks_deadline_while_streaming(monkeypatch, mock_http):
    now = [100.0]
    closed = []
    monkeypatch.setattr(fetch_module, "monotonic", lambda: now[0])

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(3):
                now[0] += 16
                yield b"partial content"

        def close(self):
            closed.append(True)

    mock_http(lambda request: httpx.Response(200, stream=SlowStream()))
    with pytest.raises(ToolError) as error:
        fetch_module._local_fetch("https://example.test")
    assert error.value.code == ToolErrorCode.TIMEOUT
    assert closed == [True]


@pytest.mark.asyncio
async def test_async_fetch_limits_total_wait(monkeypatch):
    release = Event()
    finished = Event()

    def slow_fetch(url, **kwargs):
        try:
            release.wait(2)
            return "late content"
        finally:
            finished.set()

    monkeypatch.setattr(fetch_module, "FETCH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(fetch_module, "_local_fetch", slow_fetch)
    tool = fetch_module._build_web_fetch_tool({"provider": "local"})
    try:
        with pytest.raises(ToolError) as error:
            await tool.coroutine("https://example.test")
        assert error.value.code == ToolErrorCode.TIMEOUT
        assert not finished.is_set()
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 2)
