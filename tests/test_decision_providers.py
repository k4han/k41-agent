"""Named decision providers, migration, and live client selection contracts."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agent.modules.decisions import ChoiceQuestion, DecisionService, MockDecisionClient, load_decision_settings
from agent.modules.decisions.providers import CLIENT_FACTORIES, register_decision_provider
from agent.shared.config import ConfigService
from agent.shared.config.decision_providers import (
    DECISION_PROVIDERS, MIGRATION_KEY, migration_updates, provider_entries,
)
from agent.shared.config.default_source import DefaultConfigSource
from agent.shared.infrastructure.db.runtime_settings import RuntimeSetting
from test_dashboard_settings import _db_config_service, make_dashboard_client  # noqa: F401
from test_web_connections import database_source  # noqa: F401

API = "/dashboard-api/decision-providers"


def create_body(name="cloudflare", token="private-token"):
    return {"name": name, "type": "cloudflare", "fields": {
        "account_id": "account", "api_token": token, "model": "@cf/cloudflare/clef-flash",
        "timeout": 5, "max_retries": 2,
    }}


def test_create_multiple_providers_without_changing_default(make_dashboard_client):
    service, source = _db_config_service("{}")
    client = make_dashboard_client(service)
    for name in ["cloudflare", "cloudflare-2", "work-account"]:
        assert client.post(API, json=create_body(name)).status_code == 200
    assert client.post(API, json=create_body("Cloudflare")).status_code == 409
    assert client.post(API, json=create_body("default")).status_code == 400
    assert client.post(API, json=create_body("bad.name")).status_code == 400
    payload = client.get(API).json()
    assert len(payload["providers"]) == 3
    assert payload["default_provider"] == ""
    assert source.get("decision.default_provider") is None
    assert all(entry["configured"] for entry in payload["providers"])
    assert "private-token" not in client.get(API).text
    for route in ["/settings", "/settings/sources", "/dashboard-api/decisions", "/dashboard-api/config"]:
        assert "private-token" not in client.get(route).text


def test_edit_tokens_defaults_and_delete_guards(make_dashboard_client, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    service, source = _db_config_service("{}")
    client = make_dashboard_client(service)
    assert client.post(API, json=create_body()).status_code == 200
    assert client.put(f"{API}/cloudflare", json={"fields": {"model": "custom-model"}}).status_code == 200
    assert source.get("decision.providers.cloudflare.api_token") == "private-token"
    assert client.put(f"{API}/default", json={"name": "CLOUDFLARE"}).status_code == 200
    assert source.get("decision.default_provider") == "cloudflare"
    assert client.delete(f"{API}/cloudflare").status_code == 409
    assert client.put(f"{API}/cloudflare", json={"fields": {"api_token": "replacement"}}).status_code == 200
    assert source.get("decision.providers.cloudflare.api_token") == "replacement"
    assert client.put(f"{API}/cloudflare", json={"fields": {"api_token": None}}).status_code == 200
    assert source.get("decision.providers.cloudflare.api_token") == ""
    assert client.put(f"{API}/default", json={"name": "cloudflare"}).status_code == 400
    assert client.put(f"{API}/default", json={"name": None}).status_code == 200
    assert client.delete(f"{API}/cloudflare").status_code == 200
    assert client.put(f"{API}/default", json={"name": "missing"}).status_code == 404
    assert client.put(f"{API}/missing", json={"fields": {}}).status_code == 404
    assert client.delete(f"{API}/missing").status_code == 404


@pytest.mark.parametrize("fields", [
    {"unsupported": "value"}, {"base_url": "file:///tmp"}, {"timeout": 0},
    {"timeout": "nan"}, {"timeout": True}, {"max_retries": -1}, {"max_retries": 1.5},
    {"account_id": {"nested": "value"}},
])
def test_invalid_fields_do_not_write(make_dashboard_client, fields):
    service, source = _db_config_service("{}")
    client = make_dashboard_client(service)
    assert client.post(API, json={**create_body(), "fields": fields}).status_code == 400
    assert provider_entries(source.get_all()) == {}
    assert client.post(API, json={"name": "unknown", "type": "unknown"}).status_code == 400


def test_generic_settings_cannot_bypass_provider_validation(make_dashboard_client):
    service, _ = _db_config_service("{}")
    client = make_dashboard_client(service)
    for key in ["decision.default_provider", "decision.providers.bad.type", "decision.migration_version"]:
        assert client.put(f"/settings/{key}", json={"value": "missing"}).status_code == 400
        assert client.put("/settings", json={"values": {key: "missing"}}).status_code == 400


def test_named_provider_credentials_and_environment_resolution(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "env-account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "env-token")
    service, source = _db_config_service("{}")
    source.update_settings({"decision.cloudflare.api_token": "legacy-token",
        "decision.providers.team.type": "cloudflare", "decision.providers.team.api_token": "team-token",
        "decision.providers.team.model": "team-model", "decision.default_provider": "team"})
    settings = load_decision_settings(service)
    assert settings.cloudflare_account_id == "env-account"
    assert settings.cloudflare_api_token == "team-token"
    assert settings.model == "team-model"
    source.update_setting("decision.providers.team.api_token", "")
    assert load_decision_settings(service).cloudflare_api_token == "env-token"
    source.update_setting("decision.default_provider", "missing")
    with pytest.raises(ValueError, match="not found"):
        load_decision_settings(service)
    source.update_setting("decision.default_provider", "")
    assert load_decision_settings(service).cloudflare_api_token == "legacy-token"


def test_migration_is_atomic_idempotent_and_encrypts_tokens(database_source, monkeypatch):
    source = database_source
    source.update_settings({"decision.cloudflare.account_id": "legacy-account",
        "decision.cloudflare.api_token": "legacy-secret", "decision.model": "legacy-model",
        "decision.timeout": 9.0, "decision.max_retries": 4})
    before = source.get_all()
    encode = source._encode_value

    def fail_marker(value, *, encrypted):
        if value == 1:
            raise RuntimeError("migration failed")
        return encode(value, encrypted=encrypted)

    monkeypatch.setattr(source, "_encode_value", fail_marker)
    with pytest.raises(RuntimeError, match="migration failed"):
        source.migrate_decision_providers()
    source.reload()
    assert source.get_all() == before
    monkeypatch.setattr(source, "_encode_value", encode)
    assert source.migrate_decision_providers()
    assert source.migrate_decision_providers() == set()
    service = ConfigService(sources=[DefaultConfigSource(), source])
    settings = load_decision_settings(service)
    assert settings.provider_name == "cloudflare-legacy"
    assert settings.cloudflare_api_token == "legacy-secret"
    assert settings.model == "legacy-model"
    assert settings.timeout == 9.0
    assert settings.max_retries == 4
    with source._session_maker() as session:
        row = session.execute(select(RuntimeSetting).where(RuntimeSetting.key == "decision.providers.cloudflare-legacy.api_token")).scalar_one()
        assert row.encrypted
        assert "legacy-secret" not in row.value_json


def test_migration_preserves_existing_names_and_default_without_copying_environment(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "environment-only")
    assert migration_updates({}) == {MIGRATION_KEY: 1}
    flat = {"decision.providers.cloudflare-legacy.type": "cloudflare", "decision.cloudflare.api_token": "old"}
    assert migration_updates(flat)["decision.default_provider"] == "cloudflare-legacy-2"
    flat["decision.default_provider"] = "cloudflare-legacy"
    assert migration_updates(flat) == {MIGRATION_KEY: 1}


@pytest.mark.asyncio
async def test_live_selection_preserves_inflight_client_and_metrics(monkeypatch):
    service_config, source = _db_config_service("{}")
    source.update_settings({"decision.providers.first.type": "cloudflare", "decision.providers.first.api_token": "first-token",
        "decision.providers.first.account_id": "first-account", "decision.providers.first.model": "first-model",
        "decision.providers.second.type": "cloudflare", "decision.providers.second.api_token": "second-token",
        "decision.providers.second.account_id": "second-account", "decision.providers.second.model": "second-model",
        "decision.default_provider": "first"})
    started = asyncio.Event()
    resume = asyncio.Event()
    clients = []

    class Client(MockDecisionClient):
        def __init__(self, settings):
            super().__init__()
            self.settings = settings
            self.models = []
            self.closed = False
            clients.append(self)

        async def evaluate(self, *args, **kwargs):
            self.models.append(kwargs.get("model"))
            if self.settings.provider_name == "first":
                started.set()
                await resume.wait()
            return await super().evaluate(*args, **kwargs)

        async def close(self):
            self.closed = True

    monkeypatch.setitem(CLIENT_FACTORIES, "cloudflare", Client)
    service = DecisionService(config=service_config)
    first = service.client
    task = asyncio.create_task(service.evaluate(ChoiceQuestion(instructions="Choose", criteria={"a": "A"})))
    await started.wait()
    source.update_setting("decision.default_provider", "second")
    await service.evaluate(ChoiceQuestion(instructions="Choose", criteria={"a": "A"}))
    assert service.client is not first
    assert first.closed is False
    assert clients[-1].settings.cloudflare_account_id == "second-account"
    assert clients[-1].settings.cloudflare_api_token == "second-token"
    assert clients[-1].models == ["second-model"]
    resume.set()
    await task
    assert first.models == ["first-model"]
    assert service.total_evaluations == 2
    source.update_setting("decision.providers.second.api_token", "updated-token")
    assert service.client.settings.cloudflare_api_token == "updated-token"
    await service.close()
    assert all(client.closed for client in clients)


def test_router_resolves_same_live_provider(monkeypatch):
    from agent.modules.workflows.graphs.router import _resolve_decision_client
    config, source = _db_config_service("{}")
    source.update_settings({"decision.providers.team.type": "cloudflare", "decision.providers.team.api_token": "live-token",
        "decision.providers.team.account_id": "live-account", "decision.default_provider": "team"})
    monkeypatch.setattr("agent.bootstrap.container.get_active_container", lambda: None)
    monkeypatch.setattr("agent.shared.config.get_config_service", lambda: config)
    monkeypatch.setitem(CLIENT_FACTORIES, "cloudflare", lambda settings: SimpleNamespace(settings=settings))
    assert _resolve_decision_client().settings.cloudflare_api_token == "live-token"
    source.update_setting("decision.providers.team.api_token", "changed-token")
    assert _resolve_decision_client().settings.cloudflare_api_token == "changed-token"


def test_registry_extends_catalog_forms_and_runtime(monkeypatch, make_dashboard_client):
    definition = {"label": "Future Provider", "fields": [{"name": "model", "label": "Model", "input_type": "text", "required": True}]}
    monkeypatch.setitem(DECISION_PROVIDERS, "future", definition)
    monkeypatch.setitem(CLIENT_FACTORIES, "future", lambda settings: MockDecisionClient())
    register_decision_provider("future", definition, CLIENT_FACTORIES["future"])
    service, _ = _db_config_service("{}")
    client = make_dashboard_client(service)
    assert client.post(API, json={"name": "new", "type": "future", "fields": {"model": "new-model"}}).status_code == 200
    assert client.put(f"{API}/default", json={"name": "new"}).status_code == 200
    assert any(item["type"] == "future" for item in client.get(API).json()["services"])
    assert isinstance(DecisionService(config=service).client, MockDecisionClient)


def test_ai_catalog_id_survives_renamed_connections(make_dashboard_client, monkeypatch):
    catalog = {"vendor": SimpleNamespace(id="vendor-id", provider_type="openai_compatible", name="Vendor", base_url="https://example.com/v1",
        env_vars=(), doc_url=None, models=(), default_model="model", logo_url="")}
    monkeypatch.setattr("agent.modules.providers.load_providers_catalog", lambda: catalog)
    monkeypatch.setattr("agent.delivery.http.dashboard.routes.helpers.settings.load_providers_catalog", lambda: catalog)
    config, source = _db_config_service("{}")
    client = make_dashboard_client(config)
    for name in ["personal", "team"]:
        body = {"name": name, "type": "openai_compatible", "api_key": "key", "base_url": "https://example.com/v1", "catalog_id": "vendor-id"}
        assert client.post("/dashboard-api/providers", json=body).status_code == 200
        assert source.get(f"llm.providers.{name}.catalog_id") == "vendor-id"
    assert {row["catalog_id"] for row in client.get("/dashboard-api/providers").json()["provider_rows"]} == {"vendor-id"}
    assert config.get_str("llm.default_model", "") == ""
    assert client.post("/dashboard-api/providers", json={**body, "name": "bad", "catalog_id": "unknown"}).status_code == 400
    assert client.post("/dashboard-api/providers", json={**body, "name": "bad", "type": "google"}).status_code == 400


@pytest.mark.asyncio
async def test_ai_catalog_alias_preserves_models_metadata_and_named_factory(monkeypatch):
    from agent.modules.providers.catalog import ModelCatalogEntry, ProviderCatalogEntry
    from agent.modules.providers.provider import ProviderType
    from agent.modules.providers.repository import _build_provider_config
    from agent.modules.providers.service import ProviderService

    model = ModelCatalogEntry("vendor-model", "Vendor Model", 8192, 1024, ("text",), ("text",), False, True, None, None)
    catalog = ProviderCatalogEntry("vendor", "Vendor", "openai_compatible", "https://vendor.example/v1", (), None, (model,), "vendor-model", "")
    monkeypatch.setattr("agent.modules.providers.catalog.get_provider_catalog_entry", lambda name: catalog if name == "vendor" else None)
    config = _build_provider_config("personal", {"catalog_id": "vendor", "api_key": "personal-key", "type": "openai_compatible"})
    assert config.name == "personal"
    assert config.catalog_id == "vendor"
    assert config.models == ("vendor-model",)
    assert config.base_url == "https://vendor.example/v1"
    service = ProviderService(SimpleNamespace(get_provider=lambda name: config))
    generic = SimpleNamespace()
    vendor_factory = SimpleNamespace(list_models=lambda provider, key: ["vendor-model"])
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, generic)
    service.register_provider_factory("vendor", vendor_factory)
    result = await service.list_model_catalog("personal", include_remote=True)
    assert result.provider == "personal"
    assert result.can_list_models
    assert result.models[0].context_window == 8192
    service.register_provider_factory("personal", generic)
    assert service.get_factory(ProviderType.OPENAI_COMPATIBLE, "personal", "vendor") is generic


def test_registry_password_fields_are_encrypted_and_masked(database_source, make_dashboard_client, monkeypatch):
    monkeypatch.setitem(DECISION_PROVIDERS, "future", {"label": "Future", "fields": [
        {"name": "credential", "label": "Credential", "input_type": "password", "required": True},
    ]})
    service = ConfigService(sources=[DefaultConfigSource(), database_source])
    client = make_dashboard_client(service)
    assert client.post(API, json={"name": "future", "type": "future", "fields": {"credential": "private-credential"}}).status_code == 200
    with database_source._session_maker() as session:
        row = session.execute(select(RuntimeSetting).where(RuntimeSetting.key == "decision.providers.future.credential")).scalar_one()
        assert row.encrypted
        assert "private-credential" not in row.value_json
    assert client.get(API).json()["providers"][0]["stored_secrets"] == {"credential": True}
    assert "private-credential" not in client.get("/settings").text
    assert "private-credential" not in client.get(API).text


def test_database_attachment_migrates_yaml_credentials_once(tmp_path, monkeypatch):
    from agent.shared.config import attach_database_config_source
    from agent.shared.config.database_source import DatabaseConfigSource
    from agent.shared.config.yaml_source import YamlConfigSource

    path = tmp_path / "config.yaml"
    path.write_text("decision:\n  cloudflare:\n    account_id: yaml-account\n    api_token: yaml-token\n  model: yaml-model\n", encoding="utf-8")
    database_url = f"sqlite:///{tmp_path / 'decision.db'}"
    source = DatabaseConfigSource(database_url, key_path=tmp_path / "key")
    RuntimeSetting.__table__.create(source._engine)
    source.close()
    monkeypatch.setattr("agent.shared.config.database_source._default_key_path", lambda: tmp_path / "key")
    service = ConfigService(sources=[DefaultConfigSource(), YamlConfigSource(path=path)])
    attach_database_config_source(database_url, service)
    try:
        settings = load_decision_settings(service)
        assert settings.provider_name == "cloudflare-legacy"
        assert settings.cloudflare_api_token == "yaml-token"
        assert settings.model == "yaml-model"
        assert service.get(MIGRATION_KEY) == 1
        attach_database_config_source(database_url, service)
        assert len(provider_entries(service.get_all())) == 1
    finally:
        service._sources[-1].close()


def test_verify_decision_provider_routes(make_dashboard_client, monkeypatch):
    import httpx

    service, source = _db_config_service("{}")
    client = make_dashboard_client(service)

    fake_resp = httpx.Response(
        200,
        json={"success": True, "result": {"answers": {"ping": {"noul": 0.0}}}},
        request=httpx.Request("POST", "https://api.cloudflare.com/client/v4/accounts/acc-1/ai/run/@cf/cloudflare/clef-flash"),
    )

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, headers=None, **kwargs):
            assert "ai/run/" in str(url)
            assert json is not None and "questions" in json and "state" in json
            return fake_resp

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    # 1. Direct candidate verification
    resp = client.post(
        f"{API}/verify",
        json={
            "type": "cloudflare",
            "fields": {"account_id": "acc-1", "api_token": "tok-1"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["latency_ms"] >= 0

    # 2. Saved decision provider verification
    source.update_settings({
        "decision.providers.cf-saved.type": "cloudflare",
        "decision.providers.cf-saved.account_id": "acc-saved",
        "decision.providers.cf-saved.api_token": "tok-saved",
    })
    resp_saved = client.post(f"{API}/verify", json={"name": "cf-saved"})
    assert resp_saved.status_code == 200
    assert resp_saved.json()["ok"] is True

    # 3. Missing account_id
    resp_bad = client.post(
        f"{API}/verify",
        json={"type": "cloudflare", "fields": {"api_token": "tok-only"}},
    )
    assert resp_bad.status_code == 200
    assert resp_bad.json()["ok"] is False
    assert resp_bad.json()["error_code"] == "INVALID_CONFIG"

