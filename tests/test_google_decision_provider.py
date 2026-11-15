"""Google decision provider configuration, verification, and live selection."""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from agent.modules.decisions import (
    ChoiceQuestion, DecisionService, GoogleDecisionClient, MockDecisionClient,
    create_decision_client, load_decision_settings,
)
from agent.modules.decisions.providers import CLIENT_FACTORIES
from agent.shared.config import ConfigService
from agent.shared.config.decision_providers import effective_fields
from agent.shared.config.default_source import DefaultConfigSource
from agent.shared.infrastructure.db.runtime_settings import RuntimeSetting
from test_dashboard_settings import _db_config_service, make_dashboard_client  # noqa: F401
from test_web_connections import database_source  # noqa: F401

API = "/dashboard-api/decision-providers"


@pytest.fixture(autouse=True)
def isolated_google_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def test_google_crud_encrypts_and_masks_key(database_source, make_dashboard_client):
    config = ConfigService(sources=[DefaultConfigSource(), database_source])
    dashboard = make_dashboard_client(config)
    assert dashboard.post(API, json={"name": "google-team", "type": "google", "fields": {"api_key": "private-google-key"}}).status_code == 200
    payload = dashboard.get(API).json()
    assert payload["default_provider"] == ""
    entry = payload["providers"][0]
    assert entry["configured"] is True
    assert entry["fields"]["model"] == "gemini-decision-1"
    assert "api_key" not in entry["fields"]
    assert entry["stored_secrets"] == {"api_key": True}
    with database_source._session_maker() as session:
        row = session.execute(select(RuntimeSetting).where(RuntimeSetting.key == "decision.providers.google-team.api_key")).scalar_one()
        assert row.encrypted
        assert "private-google-key" not in row.value_json
    for route in [API, "/settings", "/settings/sources", "/dashboard-api/decisions", "/dashboard-api/config"]:
        assert "private-google-key" not in dashboard.get(route).text
    assert dashboard.put(f"{API}/default", json={"name": "google-team"}).status_code == 200
    assert load_decision_settings(config).provider_type == "google"
    assert dashboard.put(f"{API}/google-team", json={"fields": {"timeout": 9}}).status_code == 200
    assert database_source.get("decision.providers.google-team.api_key") == "private-google-key"
    assert dashboard.delete(f"{API}/google-team").status_code == 409
    assert dashboard.put(f"{API}/default", json={"name": None}).status_code == 200
    assert dashboard.delete(f"{API}/google-team").status_code == 200


def test_google_environment_precedence_and_explicit_selection(monkeypatch, make_dashboard_client):
    config, source = _db_config_service("{}")
    dashboard = make_dashboard_client(config)
    assert dashboard.post(API, json={"name": "google", "type": "google"}).status_code == 200
    assert dashboard.put(f"{API}/default", json={"name": "google"}).status_code == 400
    monkeypatch.setenv("GOOGLE_API_KEY", "google-env-key")
    assert effective_fields({"type": "google"})["api_key"] == "google-env-key"
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-env-key")
    assert effective_fields({"type": "google"})["api_key"] == "gemini-env-key"
    assert effective_fields({"type": "google", "api_key": " stored-key "})["api_key"] == "stored-key"
    assert load_decision_settings(config).provider_type == "cloudflare"
    assert dashboard.get(API).json()["providers"][0]["configured"] is True
    assert dashboard.put(f"{API}/default", json={"name": "google"}).status_code == 200
    settings = load_decision_settings(config)
    assert settings.provider_fields["api_key"] == "gemini-env-key"
    assert settings.model == "gemini-decision-1"
    assert settings.timeout == 5.0
    assert settings.max_retries == 2
    assert source.get("decision.providers.google.api_key") is None


@pytest.mark.asyncio
async def test_google_factory_uses_selected_provider_settings():
    config, source = _db_config_service("{}")
    source.update_settings({
        "decision.default_provider": "team", "decision.providers.team.type": "google",
        "decision.providers.team.api_key": "team-key", "decision.providers.team.timeout": 9,
        "decision.providers.team.model": "custom-model", "decision.providers.team.base_url": "https://proxy.example/v1beta",
    })
    client = create_decision_client(load_decision_settings(config))
    try:
        assert isinstance(client, GoogleDecisionClient)
        assert client.api_key == "team-key"
        assert client.default_model == "custom-model"
        assert client.base_url == "https://proxy.example/v1beta"
        assert client.timeout == 9
    finally:
        await client.close()
    source.update_setting("decision.providers.team.api_key", "")
    assert create_decision_client(load_decision_settings(config)) is None


@pytest.mark.parametrize("candidate,expected_key", [
    ({"type": "google", "fields": {"api_key": "candidate-key"}}, "candidate-key"),
    ({"name": "saved"}, "saved-key"),
    ({"name": "saved", "fields": {"api_key": None}}, "saved-key"),
    ({"name": "saved", "fields": {"api_key": "replacement-key"}}, "replacement-key"),
    ({"type": "google"}, "env-key"),
])
def test_google_verify_uses_runtime_protocol(make_dashboard_client, monkeypatch, candidate, expected_key):
    config, source = _db_config_service("{}")
    source.update_settings({"decision.providers.saved.type": "google", "decision.providers.saved.api_key": "saved-key"})
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/decisions"
        assert request.headers["x-goog-api-key"] == expected_key
        payload = json.loads(request.content)
        assert payload["input"] == "Connection test."
        assert payload["model"] == "gemini-decision-1"
        assert payload["questions"]["ping"]["type"] == "noul"
        return httpx.Response(200, json={"answers": {"ping": {"type": "noul", "noul": 0.99, "confidence": 0.99}}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)))
    before = source.get_all()
    payload = make_dashboard_client(config).post(f"{API}/verify", json=candidate).json()
    assert payload["ok"] is True
    assert payload["details"] == {"model": "gemini-decision-1"}
    assert payload["latency_ms"] >= 0
    assert expected_key not in json.dumps(payload)
    assert source.get_all() == before
    assert len(requests) == 1


@pytest.mark.parametrize("candidate", [
    {"type": "google"},
    {"name": "saved", "fields": {"api_key": ""}},
    {"name": "cf", "type": "google"},
    {"type": "google", "fields": {"api_key": "candidate", "timeout": "nan"}},
    {"type": "google", "fields": {"api_key": "candidate", "base_url": "file:///tmp"}},
])
def test_google_verify_invalid_config_does_not_send(make_dashboard_client, monkeypatch, candidate):
    config, source = _db_config_service("{}")
    source.update_settings({
        "decision.providers.saved.type": "google", "decision.providers.saved.api_key": "saved-key",
        "decision.providers.cf.type": "cloudflare", "decision.providers.cf.api_token": "cf-token",
    })
    def fail_client(**kwargs):
        pytest.fail("Invalid verification configuration must not create an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", fail_client)
    payload = make_dashboard_client(config).post(f"{API}/verify", json=candidate).json()
    assert payload["ok"] is False
    assert payload["error_code"] == "INVALID_CONFIG"


@pytest.mark.parametrize("status,body,error_code", [
    (401, {}, "AUTH_FAILED"),
    (400, {"error": {"details": [{"reason": "API_KEY_INVALID"}]}}, "AUTH_FAILED"),
    (429, {}, "CONNECTION_ERROR"), (503, {}, "CONNECTION_ERROR"),
    (400, {"error": {"code": "safety", "message": "Content blocked"}}, "CONNECTION_ERROR"),
    (200, {"answers": {}}, "CONNECTION_ERROR"),
])
def test_google_verify_errors_are_reported_without_retries(make_dashboard_client, monkeypatch, status, body, error_code):
    config, _ = _db_config_service("{}")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)))
    payload = make_dashboard_client(config).post(f"{API}/verify", json={"type": "google", "fields": {"api_key": "candidate-key"}}).json()
    assert payload["ok"] is False
    assert payload["error_code"] == error_code
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_live_cross_provider_switch_preserves_inflight_requests(monkeypatch):
    config, source = _db_config_service("{}")
    source.update_settings({
        "decision.default_provider": "cf", "decision.providers.cf.type": "cloudflare",
        "decision.providers.cf.account_id": "account", "decision.providers.cf.api_token": "cf-token",
        "decision.providers.google.type": "google", "decision.providers.google.api_key": "google-key",
    })
    started, resume = asyncio.Event(), asyncio.Event()
    clients = []

    class Client(MockDecisionClient):
        def __init__(self, settings):
            super().__init__()
            self.settings, self.closed = settings, False
            clients.append(self)

        async def evaluate(self, *args, **kwargs):
            if self.settings.provider_type == "cloudflare":
                started.set()
                await resume.wait()
            return await super().evaluate(*args, **kwargs)

        async def close(self):
            self.closed = True

    for kind in ("cloudflare", "google"):
        monkeypatch.setitem(CLIENT_FACTORIES, kind, Client)
    question = ChoiceQuestion(instructions="Choose", criteria={"a": "A", "b": "B"})
    service = DecisionService(config=config)
    first = asyncio.create_task(service.evaluate(question))
    try:
        await started.wait()
        source.update_setting("decision.default_provider", "google")
        await service.evaluate(question)
        assert service.client.settings.provider_type == "google"
        assert clients[0].closed is False
        source.update_setting("decision.default_provider", "cf")
        assert service.client.settings.provider_type == "cloudflare"
        assert service.client is not clients[0]
    finally:
        resume.set()
        await first
        await service.close()
    assert service.total_evaluations == 2
    assert all(client.closed for client in clients)
