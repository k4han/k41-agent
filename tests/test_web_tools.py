from agent.modules.tools.builtin.web import web_fetch as web_fetch_module
from agent.modules.tools.builtin.web import web_search as web_search_module
from agent.modules.tools.decorators import META_ATTR
from agent.modules.tools.result import ToolError, ToolErrorCode


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
    monkeypatch.setattr(web_search_module, "_google_search", lambda *args, **kwargs: None)

    result = web_search_module.web_search.func("example", 1)

    assert "Example" in result
    assert "https://example.com" in result
    assert "Example snippet" in result


def test_web_search_registers_config_schema_with_secret_key():
    meta = getattr(web_search_module.web_search, META_ATTR)
    assert meta.config_schema is not None
    fields = meta.config_schema.field_map()
    assert set(fields) == {
        "provider",
        "google_api_key",
        "google_cse_id",
        "tavily_api_key",
        "bing_api_key",
        "brave_api_key",
        "firecrawl_api_key",
        "firecrawl_base_url",
    }
    assert fields["google_api_key"].secret is True
    assert fields["google_api_key"].input_type == "password"
    assert fields["google_api_key"].show_when == {"provider": ("auto", "google")}
    assert fields["tavily_api_key"].secret is True
    assert fields["tavily_api_key"].show_when == {"provider": ("auto", "tavily")}
    assert fields["bing_api_key"].show_when == {"provider": ("auto", "bing")}
    assert fields["brave_api_key"].show_when == {"provider": ("auto", "brave")}
    assert fields["firecrawl_api_key"].secret is True
    assert fields["firecrawl_api_key"].show_when == {"provider": ("auto", "firecrawl")}
    assert fields["firecrawl_base_url"].show_when == {"provider": ("auto", "firecrawl")}
    assert meta.factory is web_search_module._build_web_search_tool


def test_web_search_google_mode_requires_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_ID", raising=False)

    tool = web_search_module._build_web_search_tool({"provider": "google"})
    try:
        tool.func("example")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.INVALID_INPUT
        assert "web_search" in str(exc)
    else:
        raise AssertionError("expected ToolError for missing Google credentials")


def test_web_search_uses_configured_credentials(monkeypatch):
    seen: dict[str, object] = {}

    def _fake_google(query, num_results=5, **kwargs):
        seen.update(kwargs)
        return "google-result"

    monkeypatch.setattr(web_search_module, "_google_search", _fake_google)

    tool = web_search_module._build_web_search_tool(
        {
            "provider": "google",
            "google_api_key": "key-123",
            "google_cse_id": "cse-456",
        }
    )
    assert tool.func("example") == "google-result"
    assert seen == {"api_key": "key-123", "cse_id": "cse-456"}


def test_web_search_falls_back_to_env_credentials(monkeypatch):
    seen: dict[str, object] = {}

    def _fake_google(query, num_results=5, **kwargs):
        seen.update(kwargs)
        return "google-result"

    monkeypatch.setattr(web_search_module, "_google_search", _fake_google)
    monkeypatch.setenv("GOOGLE_API_KEY", "env-key")
    monkeypatch.setenv("GOOGLE_CSE_ID", "env-cse")

    tool = web_search_module._build_web_search_tool({})
    assert tool.func("example") == "google-result"
    assert seen == {"api_key": "env-key", "cse_id": "env-cse"}


def test_web_search_auto_falls_back_to_duckduckgo_on_google_failure(monkeypatch):
    def _failing_google(*args, **kwargs):
        raise ToolError(ToolErrorCode.UPSTREAM, "google down")

    monkeypatch.setattr(web_search_module, "_google_search", _failing_google)
    monkeypatch.setattr(
        web_search_module, "_duckduckgo_search", lambda *args: "ddg-result"
    )

    tool = web_search_module._build_web_search_tool(
        {
            "provider": "auto",
            "google_api_key": "key-123",
            "google_cse_id": "cse-456",
        }
    )
    assert tool.func("example") == "ddg-result"


def test_web_search_explicit_google_propagates_upstream_error(monkeypatch):
    def _failing_google(*args, **kwargs):
        raise ToolError(ToolErrorCode.UPSTREAM, "google down")

    monkeypatch.setattr(web_search_module, "_google_search", _failing_google)

    tool = web_search_module._build_web_search_tool(
        {
            "provider": "google",
            "google_api_key": "key-123",
            "google_cse_id": "cse-456",
        }
    )
    try:
        tool.func("example")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.UPSTREAM
    else:
        raise AssertionError("expected ToolError to propagate in google mode")


def test_materialized_web_search_applies_error_normalization(monkeypatch):
    from agent.modules.tools import ToolSource, find_descriptors, materialize_tool
    from agent.modules.tools.middleware.base import MIDDLEWARE_APPLIED_ATTR

    def _failing_google(*args, **kwargs):
        raise ToolError(ToolErrorCode.UPSTREAM, "google down")

    monkeypatch.setattr(web_search_module, "_google_search", _failing_google)

    descriptor = next(
        item
        for item in find_descriptors(source=ToolSource.BUILTIN)
        if item.name == "web_search"
    )
    tool = materialize_tool(
        descriptor,
        {
            "web_search": {
                "provider": "google",
                "google_api_key": "key-123",
                "google_cse_id": "cse-456",
            }
        },
    )

    assert getattr(tool, MIDDLEWARE_APPLIED_ATTR, False)
    assert tool.invoke({"query": "example"}) == "[error] upstream: google down"


def test_web_search_tavily_mode(monkeypatch):
    seen = {}

    def _fake_tavily(query, num_results=5, **kwargs):
        seen.update(kwargs)
        return "tavily-result"

    monkeypatch.setattr(web_search_module, "_tavily_search", _fake_tavily)

    # Missing credentials
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    tool_missing = web_search_module._build_web_search_tool({"provider": "tavily"})
    try:
        tool_missing.func("example")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.INVALID_INPUT
        assert "Tavily" in str(exc)
    else:
        raise AssertionError("expected ToolError for missing Tavily key")

    # Configured credentials
    tool_configured = web_search_module._build_web_search_tool(
        {"provider": "tavily", "tavily_api_key": "tavily-123"}
    )
    assert tool_configured.func("example") == "tavily-result"
    assert seen == {"api_key": "tavily-123"}

    # Env credentials fallback
    seen.clear()
    monkeypatch.setenv("TAVILY_API_KEY", "env-tavily")
    tool_env = web_search_module._build_web_search_tool({"provider": "tavily"})
    assert tool_env.func("example") == "tavily-result"
    assert seen == {"api_key": "env-tavily"}


def test_web_search_bing_mode(monkeypatch):
    seen = {}

    def _fake_bing(query, num_results=5, **kwargs):
        seen.update(kwargs)
        return "bing-result"

    monkeypatch.setattr(web_search_module, "_bing_search", _fake_bing)

    # Missing credentials
    monkeypatch.delenv("BING_API_KEY", raising=False)
    tool_missing = web_search_module._build_web_search_tool({"provider": "bing"})
    try:
        tool_missing.func("example")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.INVALID_INPUT
        assert "Bing" in str(exc)
    else:
        raise AssertionError("expected ToolError for missing Bing key")

    # Configured credentials
    tool_configured = web_search_module._build_web_search_tool(
        {"provider": "bing", "bing_api_key": "bing-123"}
    )
    assert tool_configured.func("example") == "bing-result"
    assert seen == {"api_key": "bing-123"}


def test_web_search_brave_mode(monkeypatch):
    seen = {}

    def _fake_brave(query, num_results=5, **kwargs):
        seen.update(kwargs)
        return "brave-result"

    monkeypatch.setattr(web_search_module, "_brave_search", _fake_brave)

    # Missing credentials
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    tool_missing = web_search_module._build_web_search_tool({"provider": "brave"})
    try:
        tool_missing.func("example")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.INVALID_INPUT
        assert "Brave" in str(exc)
    else:
        raise AssertionError("expected ToolError for missing Brave key")

    # Configured credentials
    tool_configured = web_search_module._build_web_search_tool(
        {"provider": "brave", "brave_api_key": "brave-123"}
    )
    assert tool_configured.func("example") == "brave-result"
    assert seen == {"api_key": "brave-123"}


def test_web_search_firecrawl_mode(monkeypatch):
    seen = {}

    def _fake_firecrawl(query, num_results=5, **kwargs):
        seen.update(kwargs)
        return "firecrawl-result"

    monkeypatch.setattr(web_search_module, "_firecrawl_search", _fake_firecrawl)

    # Missing credentials raises INVALID_INPUT
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_BASE_URL", raising=False)
    tool_missing = web_search_module._build_web_search_tool({"provider": "firecrawl"})
    try:
        tool_missing.func("example")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.INVALID_INPUT
        assert "Firecrawl" in str(exc)
    else:
        raise AssertionError("expected ToolError for missing Firecrawl credentials")

    # Self-hosted base URL alone is accepted (keyless)
    seen.clear()
    tool_self_hosted = web_search_module._build_web_search_tool(
        {"provider": "firecrawl", "firecrawl_base_url": "https://custom.firecrawl.local/v2"}
    )
    assert tool_self_hosted.func("example") == "firecrawl-result"
    assert seen == {"api_key": "", "base_url": "https://custom.firecrawl.local/v2"}

    # Configured credentials
    seen.clear()
    tool_configured = web_search_module._build_web_search_tool(
        {
            "provider": "firecrawl",
            "firecrawl_api_key": "fc-key-123",
            "firecrawl_base_url": "https://custom.firecrawl.local/v2",
        }
    )
    assert tool_configured.func("example") == "firecrawl-result"
    assert seen == {
        "api_key": "fc-key-123",
        "base_url": "https://custom.firecrawl.local/v2",
    }

    # Env credentials fallback
    seen.clear()
    monkeypatch.setenv("FIRECRAWL_API_KEY", "env-fc-key")
    monkeypatch.setenv("FIRECRAWL_BASE_URL", "https://env.firecrawl.local/v2")
    tool_env = web_search_module._build_web_search_tool({"provider": "firecrawl"})
    assert tool_env.func("example") == "firecrawl-result"
    assert seen == {
        "api_key": "env-fc-key",
        "base_url": "https://env.firecrawl.local/v2",
    }


def test_firecrawl_search_url_resolution(monkeypatch):
    recorded_urls = []

    class _CaptureClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def post(self, url, json, **kwargs):
            recorded_urls.append(url)
            import httpx
            req = httpx.Request("POST", url)
            return httpx.Response(200, request=req, json={"success": True, "data": {"web": []}})

    monkeypatch.setattr(web_search_module.httpx, "Client", _CaptureClient)

    # Empty base URL -> defaults to official search endpoint
    web_search_module._firecrawl_search("test", base_url="")
    assert recorded_urls[-1] == "https://api.firecrawl.dev/v2/search"

    # Whitespace base URL -> defaults to official search endpoint
    web_search_module._firecrawl_search("test", base_url="   ")
    assert recorded_urls[-1] == "https://api.firecrawl.dev/v2/search"

    # Trailing slash base URL
    web_search_module._firecrawl_search("test", base_url="https://api.firecrawl.dev/v2/")
    assert recorded_urls[-1] == "https://api.firecrawl.dev/v2/search"

    # Already containing /search
    web_search_module._firecrawl_search("test", base_url="https://api.firecrawl.dev/v2/search")
    assert recorded_urls[-1] == "https://api.firecrawl.dev/v2/search"

    # Custom self-hosted instance
    web_search_module._firecrawl_search("test", base_url="http://localhost:3002")
    assert recorded_urls[-1] == "http://localhost:3002/search"


def test_firecrawl_search_network_errors(monkeypatch):
    import httpx

    class _TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(web_search_module.httpx, "Client", _TimeoutClient)
    try:
        web_search_module._firecrawl_search("test")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.TIMEOUT
        assert "timed out" in str(exc)
    else:
        raise AssertionError("expected Timeout ToolError")

    class _StatusClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def post(self, *args, **kwargs):
            req = httpx.Request("POST", "https://api.firecrawl.dev/v2/search")
            resp = httpx.Response(401, request=req)
            raise httpx.HTTPStatusError("unauthorized", request=req, response=resp)

    monkeypatch.setattr(web_search_module.httpx, "Client", _StatusClient)
    try:
        web_search_module._firecrawl_search("test")
    except ToolError as exc:
        assert exc.code == ToolErrorCode.UPSTREAM
        assert "401" in str(exc)
    else:
        raise AssertionError("expected UPSTREAM ToolError")


def test_web_search_auto_cascades_through_providers(monkeypatch):
    calls = []

    def _failing_google(*args, **kwargs):
        calls.append("google")
        raise ToolError(ToolErrorCode.UPSTREAM, "google down")

    def _failing_tavily(*args, **kwargs):
        calls.append("tavily")
        raise ToolError(ToolErrorCode.UPSTREAM, "tavily down")

    def _failing_firecrawl(*args, **kwargs):
        calls.append("firecrawl")
        raise ToolError(ToolErrorCode.UPSTREAM, "firecrawl down")

    def _failing_brave(*args, **kwargs):
        calls.append("brave")
        raise ToolError(ToolErrorCode.UPSTREAM, "brave down")

    def _fake_bing(*args, **kwargs):
        calls.append("bing")
        return "bing-cascade-result"

    monkeypatch.setattr(web_search_module, "_google_search", _failing_google)
    monkeypatch.setattr(web_search_module, "_tavily_search", _failing_tavily)
    monkeypatch.setattr(web_search_module, "_firecrawl_search", _failing_firecrawl)
    monkeypatch.setattr(web_search_module, "_brave_search", _failing_brave)
    monkeypatch.setattr(web_search_module, "_bing_search", _fake_bing)

    tool = web_search_module._build_web_search_tool(
        {
            "provider": "auto",
            "google_api_key": "g-key",
            "google_cse_id": "g-cse",
            "tavily_api_key": "t-key",
            "firecrawl_api_key": "fc-key",
            "brave_api_key": "br-key",
            "bing_api_key": "bi-key",
        }
    )
    assert tool.func("example") == "bing-cascade-result"
    assert calls == ["google", "tavily", "firecrawl", "brave", "bing"]


def test_tool_config_field_show_when_serialization():
    from agent.modules.tools.domain import ToolConfigField

    field = ToolConfigField(
        name="test_key",
        input_type="password",
        label="Test Key",
        show_when={"provider": ("auto", "google")},
    )
    d = field.to_dict()
    assert d["show_when"] == {"provider": ["auto", "google"]}
