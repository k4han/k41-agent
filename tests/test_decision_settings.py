"""Dashboard selection of configured Decision and routing LLM models."""

from types import SimpleNamespace

import pytest

from test_dashboard_settings import _db_config_service, make_dashboard_client  # noqa: F401
from test_decision_providers import create_body

API = "/dashboard-api/decisions"
LLM_KEY = "decision.router.llm_model"


@pytest.fixture
def configured_decisions(make_dashboard_client):
    service, source = _db_config_service("""
        llm:
          default_model: main/chat-model
          providers:
            main:
              type: openai_compatible
              api_key: private-llm-key
              base_url: https://example.com/v1
              default_model: chat-model
              models: [chat-model, org/router-model, chat-model]
            google:
              type: google
              api_key: private-google-key
              default_model: chat-model
            disabled:
              type: google
              api_key: private-disabled-key
              default_model: disabled-model
              enabled: false
            incomplete:
              type: google
              default_model: incomplete-model
            missing-url:
              type: openai_compatible
              api_key: private-missing-url-key
              default_model: missing-url-model
    """)
    client = make_dashboard_client(service)
    assert client.post("/dashboard-api/decision-providers", json=create_body("team")).status_code == 200
    return client, service, source


def test_decisions_lists_only_configured_ready_models(configured_decisions):
    client, _, source = configured_decisions
    source.update_setting("llm.providers.main.models", "chat-model, org/router-model\nextra-model, chat-model")
    response = client.get(API)
    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_model_options"] == [
        {"value": "google/chat-model", "label": "google / chat-model"},
        {"value": "main/chat-model", "label": "main / chat-model"},
        {"value": "main/org/router-model", "label": "main / org/router-model"},
        {"value": "main/extra-model", "label": "main / extra-model"},
    ]
    assert payload["decision_providers"]["providers"][0]["name"] == "team"
    assert payload["settings"][LLM_KEY]["value"] == ""
    assert payload["settings"]["decision.default_provider"]["label"] == "Decision Model"
    assert set(payload["settings"]) == {
        "decision.default_provider", LLM_KEY, "decision.router.mode",
        "decision.router.threshold", "decision.router.log_telemetry",
    }
    assert "private-" not in response.text
    assert "private-token" not in response.text


def test_decisions_saves_together_and_shares_default(configured_decisions):
    client, service, _ = configured_decisions
    values = {
        "decision.default_provider": " TEAM ", LLM_KEY: "MAIN/org/router-model",
        "decision.router.mode": "llm_only", "decision.router.threshold": 0.9,
    }
    response = client.put(API, json={"values": values})
    assert response.status_code == 200, response.text
    saved = client.get(API).json()["settings"]
    assert saved["decision.default_provider"]["value"] == "team"
    assert saved[LLM_KEY]["value"] == "main/org/router-model"
    assert saved["decision.router.mode"]["value"] == "llm_only"
    assert saved["decision.router.threshold"]["value"] == 0.9
    assert service.get("llm.default_model") == "main/chat-model"
    assert client.get("/dashboard-api/decision-providers").json()["default_provider"] == "team"
    assert client.put("/dashboard-api/decision-providers/default", json={"name": None}).status_code == 200
    assert client.get(API).json()["settings"]["decision.default_provider"]["value"] == ""
    assert client.put(API, json={"values": {LLM_KEY: None}}).status_code == 200
    assert service.get(LLM_KEY) == ""


@pytest.mark.parametrize("key,value,status", [
    ("decision.default_provider", "missing", 404),
    ("decision.default_provider", 123, 400),
    (LLM_KEY, "main/unconfigured-model", 400),
    (LLM_KEY, "disabled/disabled-model", 400),
    (LLM_KEY, "incomplete/incomplete-model", 400),
    (LLM_KEY, "missing-url/missing-url-model", 400),
    (LLM_KEY, "@pool/example", 400),
    (LLM_KEY, ["main/chat-model"], 400),
    ("decision.router.threshold", 1.2, 400),
    ("decision.router.threshold", True, 400),
    ("decision.router.mode", "unknown", 400),
    ("decision.router.log_telemetry", "yes", 400),
    ("decision.providers.team.api_token", "replacement", 400),
    ("llm.default_model", "google/chat-model", 400),
])
def test_decisions_invalid_update_writes_nothing(configured_decisions, key, value, status):
    client, _, source = configured_decisions
    before = source.get_all().copy()
    response = client.put(API, json={"values": {
        "decision.default_provider": "team", "decision.router.threshold": 0.85, key: value,
    }})
    assert response.status_code == status, response.text
    assert source.get_all() == before


def test_decisions_rechecks_connections_before_save(configured_decisions, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    client, _, source = configured_decisions
    assert client.get(API).status_code == 200
    source.update_setting("decision.providers.team.api_token", "")
    response = client.put(API, json={"values": {
        "decision.default_provider": "team", "decision.router.mode": "shadow",
    }})
    assert response.status_code == 400
    assert source.get("decision.router.mode") is None
    source.delete_setting_tree("decision.providers.team")
    assert client.put(API, json={"values": {"decision.default_provider": "team"}}).status_code == 404
    source.update_setting("llm.providers.main.enabled", False)
    assert client.put(API, json={"values": {LLM_KEY: "main/chat-model"}}).status_code == 400


def test_router_llm_validation_covers_generic_settings(configured_decisions):
    client, service, source = configured_decisions
    for path, body in [
        (f"/settings/{LLM_KEY}", {"value": "main/unknown"}),
        ("/settings", {"values": {LLM_KEY: "main/unknown", "decision.router.mode": "shadow"}}),
        ("/settings", {"values": {LLM_KEY: "main/chat-model", "llm.providers.main.enabled": False}}),
    ]:
        before = source.get_all().copy()
        assert client.put(path, json=body).status_code == 400
        assert source.get_all() == before
    response = client.put("/settings", json={"values": {
        LLM_KEY: "MAIN/new-model", "llm.providers.main.models": ["new-model"],
    }})
    assert response.status_code == 200, response.text
    assert service.get(LLM_KEY) == "main/new-model"
    assert client.put(f"/settings/{LLM_KEY}", json={"value": ""}).status_code == 200
    assert service.get(LLM_KEY) == ""


def test_decisions_keeps_unavailable_saved_selection(configured_decisions):
    client, _, source = configured_decisions
    source.update_setting(LLM_KEY, "removed/old-model")
    payload = client.get(API).json()
    assert payload["settings"][LLM_KEY]["value"] == "removed/old-model"
    assert "removed/old-model" not in {option["value"] for option in payload["llm_model_options"]}
    assert client.put(API, json={"values": {"decision.router.mode": "llm_only"}}).status_code == 200


def test_decisions_chatgpt_choices_follow_session_readiness(make_dashboard_client):
    service, _ = _db_config_service("""
        llm:
          providers:
            personal:
              type: chatgpt
              default_model: account-model
              models: [account-model, second-model]
    """)
    client = make_dashboard_client(service)
    session = {"connected": False, "sharing": False}
    client.app.state.container = SimpleNamespace(
        chatgpt_auth_manager=SimpleNamespace(session=lambda name: session),
    )
    assert client.get(API).json()["llm_model_options"] == []
    assert client.put(API, json={"values": {LLM_KEY: "personal/account-model"}}).status_code == 400
    session.update(connected=True, sharing=True)
    assert len(client.get(API).json()["llm_model_options"]) == 2
    assert client.put(API, json={"values": {LLM_KEY: "personal/account-model"}}).status_code == 200
    session.update(connected=False, sharing=False)
    assert client.put(API, json={"values": {LLM_KEY: "personal/second-model"}}).status_code == 400


def test_decisions_requires_writable_source(make_dashboard_client):
    client = make_dashboard_client()
    assert client.put(API, json={"values": {"decision.router.mode": "llm_only"}}).status_code == 503
    assert client.put(API, json={"values": {}}).status_code == 200
