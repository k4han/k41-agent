from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


@dataclass
class _CapturedRequest:
    method: str
    url: str


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient stub that captures the request URL."""

    def __init__(self, *, timeout: Any = None) -> None:
        self.requests: list[_CapturedRequest] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(_CapturedRequest(method="GET", url=url))
        if "search/issues" in url:
            return _FakeResponse({"items": [], "total_count": 0}, url)
        return _FakeResponse([], url)

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(_CapturedRequest(method="POST", url=url))
        return _FakeResponse({"token": "stub", "expires_at": "2099-01-01T00:00:00Z"}, "https://api.github.com")


class _FakeResponse:
    def __init__(self, payload: Any, url: str) -> None:
        self._payload = payload
        self.url = url
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


@pytest.fixture
def captured_client(monkeypatch: pytest.MonkeyPatch):
    from agent.modules.github import client as client_module
    from agent.modules.github.config import GitHubSettings

    fake = _FakeAsyncClient()
    monkeypatch.setattr(client_module.httpx, "AsyncClient", lambda *args, **kwargs: fake)
    client = client_module.GitHubAppClient.__new__(client_module.GitHubAppClient)
    client._tokens = {}
    client._settings = GitHubSettings(
        enabled=True,
        app_id="123",
        app_slug="test-app",
        private_key="test-key",
        private_key_path="",
        webhook_secret="test-secret",
        default_agent="default",
        trigger_label="k41-agent",
        mention_triggers=("@k41-agent",),
        default_workspace_backend="local",
    )
    # Mock get_installation_token to avoid JWT encoding
    async def _fake_get_installation_token(installation_id: int) -> str:
        return f"fake-token-{installation_id}"
    monkeypatch.setattr(client, "get_installation_token", _fake_get_installation_token)
    return client, fake


@pytest.mark.asyncio
async def test_list_pull_requests_for_issue_uses_search_api(
    captured_client: tuple[Any, _FakeAsyncClient],
) -> None:
    client, fake = captured_client

    await client.list_pull_requests_for_issue(
        installation_id=10,
        full_name="octo/example",
        issue_number=7,
    )

    requested_urls = [request.url for request in fake.requests if "search/issues" in request.url]
    assert requested_urls, "Expected a request to the search API"
    assert any("search/issues" in url for url in requested_urls), (
        "GitHub endpoint must use /search/issues; "
        f"got: {requested_urls}"
    )
