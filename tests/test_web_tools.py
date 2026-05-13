from agent.modules.tools.langchain.web_tools import web_fetch as web_fetch_module
from agent.modules.tools.langchain.web_tools import web_search as web_search_module


class _FakeResponse:
    encoding = "utf-8"
    headers: dict[str, str] = {}

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        yield from self._chunks

    def read(self) -> bytes:
        return b"".join(self._chunks)


def test_read_limited_response_reads_only_max_bytes(monkeypatch):
    monkeypatch.setattr(web_fetch_module, "MAX_RESPONSE_BYTES", 5)

    response = _FakeResponse([b"ab", b"cdef", b"gh"])

    assert web_fetch_module._read_limited_response(response) == b"abcde"


def test_duckduckgo_search_reads_stream_without_read_size(monkeypatch):
    html = b"""
    <html>
      <body>
        <div class="result">
          <a class="result__a" href="https://example.com">Example</a>
          <a class="result__snippet">Example snippet</a>
        </div>
      </body>
    </html>
    """

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, data):
            assert method == "POST"
            assert url == web_search_module.DDGS_HTML_URL
            assert data == {"q": "example"}
            return _FakeResponse([html])

    monkeypatch.setattr(web_search_module.httpx, "Client", _FakeClient)
    monkeypatch.setattr(web_search_module, "_google_search", lambda *args: None)

    result = web_search_module.web_search.func("example", 1)

    assert "Example" in result
    assert "https://example.com" in result
    assert "Example snippet" in result
