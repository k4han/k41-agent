"""Priority pools: availability, recovery, streaming boundaries and management."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from pydantic import ValidationError

from agent.bootstrap.container import AppContainer
from agent.modules.providers.model_pools import (
    ModelPoolConfig, ModelPoolExhausted, ModelPoolService, OutputGuard, POOL_PREFIX, pool_call_config,
)
from agent.modules.providers.models import ResolvedChatModel
from agent.modules.providers.pool_errors import classify_pool_failure
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.shared.config import ConfigService
from agent.shared.config.default_source import DefaultConfigSource


class MemorySource:
    priority = 200

    def __init__(self, values):
        self.values = values

    def get(self, key):
        return self.values.get(key)

    def get_all(self):
        return dict(self.values)

    def reload(self):
        pass

    def can_update_key(self, key):
        from agent.shared.config.constants import is_database_runtime_key

        return is_database_runtime_key(key)

    def update_settings(self, values):
        self.values.update(values)

    def update_setting(self, key, value):
        self.update_settings({key: value})

    def delete_setting_tree(self, key):
        deleted = False
        for candidate in list(self.values):
            if candidate == key or candidate.startswith(key + "."):
                del self.values[candidate]
                deleted = True
        return deleted


def api_error(status=429, code="rate_limit_exceeded", *, retry_after=None):
    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    response = httpx.Response(status, headers=headers, json={"error": {"code": code}},
                              request=httpx.Request("POST", "https://example.com/chat"))
    return httpx.HTTPStatusError("Provider failed", request=response.request, response=response)


@pytest.fixture
def pool_setup(monkeypatch):
    source = MemorySource({
        POOL_PREFIX + "small": {"members": [{"provider": "a", "model": "first"},
                                            {"provider": "b", "model": "second"}]},
        "llm.default_model": "a/first",
        "llm.providers.a.type": "openai_compatible", "llm.providers.a.api_key": "test-a",
        "llm.providers.a.base_url": "https://example.com/v1", "llm.providers.a.default_model": "first",
        "llm.providers.b.type": "google", "llm.providers.b.api_key": "test-b",
        "llm.providers.b.default_model": "second",
    })
    config = ConfigService([DefaultConfigSource(), source])
    now = [1000.0]
    sleeps = []
    providers = {name: ProviderConfig(name=name, provider_type=kind, base_url="", api_key="key",
                                     default_model=model)
                 for name, kind, model in [("a", ProviderType.OPENAI_COMPATIBLE, "first"),
                                            ("b", ProviderType.GOOGLE, "second")]}

    async def sleep(delay):
        sleeps.append(delay)

    service = ModelPoolService(config, SimpleNamespace(get_provider=lambda name: providers[name]),
                               clock=lambda: now[0], sleep=sleep)
    resolutions = []

    def resolve(name, member, temperature=None):
        resolutions.append((member.provider, temperature))
        return ResolvedChatModel(model=SimpleNamespace(), provider_name=member.provider,
                                 provider_type=str(providers[member.provider].provider_type),
                                 model_name=member.model, pool_name=name,
                                 context_window=200000 if member.provider == "a" else 32000)

    monkeypatch.setattr(service, "_resolve", resolve)
    return SimpleNamespace(service=service, config=config, source=source, now=now,
                           sleeps=sleeps, providers=providers, resolutions=resolutions)


@pytest.mark.asyncio
async def test_priority_retry_cooldown_and_recovery(pool_setup):
    setup = pool_setup
    calls = []

    async def attempt(model):
        calls.append(model.provider_name)
        if model.provider_name == "a":
            raise api_error()
        return model.model_name

    assert await setup.service.execute("small", attempt) == "second"
    assert calls == ["a", "a", "a", "b"]
    assert setup.sleeps == [1, 2]
    calls.clear()
    assert await setup.service.execute("small", attempt) == "second"
    assert calls == ["b"]
    setup.now[0] += 60
    assert await setup.service.execute("small", lambda model: asyncio.sleep(0, result=model.provider_name)) == "a"
    assert setup.service.status("small")[0]["error_code"] is None


@pytest.mark.asyncio
async def test_maximum_configured_retries_use_capped_backoff(pool_setup):
    pool_setup.source.values[POOL_PREFIX + "small"]["retry_count"] = 5
    calls = []

    async def attempt(model):
        calls.append(model.provider_name)
        if model.provider_name == "a":
            raise api_error()
        return "ok"

    assert await pool_setup.service.execute("small", attempt) == "ok"
    assert calls == [*(["a"] * 6), "b"]
    assert pool_setup.sleeps == [1, 2, 4, 8, 10]


@pytest.mark.asyncio
async def test_nested_direct_auxiliary_call_does_not_inherit_pool_guard(pool_setup):
    from agent.modules.providers.model_pools import execute_model_call

    direct = SimpleNamespace(pool_name=None)

    async def auxiliary(model):
        assert model is direct
        assert "pool_name" not in pool_call_config({}).get("metadata", {})
        assert "callbacks" not in pool_call_config({})

    async def attempt(model):
        await execute_model_call(direct, auxiliary)
        assert pool_call_config({})["metadata"]["pool_name"] == "small"
        if model.provider_name == "a":
            raise api_error(402)
        return "ok"

    assert await pool_setup.service.execute("small", attempt) == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,cooldown", [(429, "insufficient_quota", 3600), (401, "invalid_api_key", 60),
                                                (404, "model_not_found", 60)])
async def test_unavailable_models_switch_without_retry(pool_setup, status, code, cooldown):
    calls = []

    async def attempt(model):
        calls.append(model.provider_name)
        if model.provider_name == "a":
            raise api_error(status, code)
        return "ok"

    assert await pool_setup.service.execute("small", attempt) == "ok"
    assert calls == ["a", "b"]
    assert not pool_setup.sleeps
    assert pool_setup.service._states[("small", "a", "first")].retry_at == 1000 + cooldown


@pytest.mark.asyncio
@pytest.mark.parametrize("delay,expected_calls", [(3, ["a", "a", "a", "b"]), (30, ["a", "b"])])
async def test_retry_after_controls_waiting_and_deadline(pool_setup, delay, expected_calls):
    calls = []

    async def attempt(model):
        calls.append(model.provider_name)
        if model.provider_name == "a":
            raise api_error(retry_after=delay)
        return "ok"

    await pool_setup.service.execute("small", attempt)
    assert calls == expected_calls
    assert pool_setup.sleeps == ([delay, delay] if delay <= 10 else [])
    assert pool_setup.service._states[("small", "a", "first")].retry_at == 1000 + delay


@pytest.mark.asyncio
async def test_all_failed_exhaustion_reset_and_process_isolation(pool_setup):
    async def fail(model):
        raise api_error(402, "insufficient_balance")

    with pytest.raises(ModelPoolExhausted) as info:
        await pool_setup.service.execute("small", fail)
    assert info.value.code == "model_pool_exhausted"
    assert info.value.retry_at is not None
    before = len(pool_setup.resolutions)
    with pytest.raises(ModelPoolExhausted):
        await pool_setup.service.execute("small", fail)
    assert len(pool_setup.resolutions) == before
    fresh = ModelPoolService(pool_setup.config, pool_setup.service.providers)
    assert not fresh._states
    pool_setup.service.reset("small")
    assert await pool_setup.service.execute("small", lambda m: asyncio.sleep(0, result=m.provider_name)) == "a"


@pytest.mark.asyncio
async def test_only_one_recovery_probe_and_cancellation_releases_lease(pool_setup):
    async def initial(model):
        if model.provider_name == "a":
            raise api_error(402)
        return "b"

    await pool_setup.service.execute("small", initial)
    pool_setup.now[0] += 3600
    entered = asyncio.Event()

    async def probe(model):
        if model.provider_name == "a":
            entered.set()
            await asyncio.Event().wait()
        return model.provider_name

    pending = asyncio.create_task(pool_setup.service.execute("small", probe))
    await entered.wait()
    assert await pool_setup.service.execute("small", probe) == "b"
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not pool_setup.service._states[("small", "a", "first")].probing


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [AIMessageChunk(content="Hello"), AIMessageChunk(content=[{"type": "thinking", "thinking": "reason"}]),
                                  AIMessageChunk(content="", tool_call_chunks=[{"name": "read", "args": "", "id": "1", "index": 0}])])
async def test_no_retry_or_switch_after_output(pool_setup, chunk):
    calls = []

    async def attempt(model):
        calls.append(model.provider_name)
        config = pool_call_config({})
        config["callbacks"][-1].on_llm_new_token("", chunk=ChatGenerationChunk(message=chunk))
        raise api_error()

    with pytest.raises(httpx.HTTPStatusError):
        await pool_setup.service.execute("small", attempt)
    assert calls == ["a"]
    assert not pool_setup.sleeps
    assert not pool_setup.service.status("small")[0]["available"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("Bad request"), api_error(400, "context_length_exceeded"),
                                  api_error(403, "content_policy_violation"), asyncio.CancelledError()])
async def test_non_availability_errors_and_cancellation_do_not_fallback(pool_setup, error):
    calls = []

    async def attempt(model):
        calls.append(model.provider_name)
        raise error

    with pytest.raises(type(error)):
        await pool_setup.service.execute("small", attempt)
    assert calls == ["a"]
    assert pool_setup.service.status("small")[0]["available"]


@pytest.mark.asyncio
async def test_disabled_provider_configuration_edit_and_preparation_per_member(pool_setup):
    pool_setup.providers["a"] = replace(pool_setup.providers["a"], enabled=False)
    assert await pool_setup.service.execute("small", lambda m: asyncio.sleep(0, result=m.context_window)) == 32000
    pool_setup.providers["a"] = replace(pool_setup.providers["a"], enabled=True)
    prepared = []

    async def attempt(model):
        prepared.append((model.provider_name, model.context_window, pool_call_config({})["metadata"]["pool_name"]))
        if model.provider_name == "a":
            raise api_error(402)
        return "ok"

    await pool_setup.service.execute("small", attempt, temperature=0.2)
    assert prepared == [("a", 200000, "small"), ("b", 32000, "small")]
    pool_setup.source.values["llm.providers.a.api_key"] = "updated"
    assert await pool_setup.service.execute("small", lambda m: asyncio.sleep(0, result=m.provider_name)) == "a"


def test_config_validation_and_error_normalization():
    for config in [
        {"members": []},
        {"members": [{"provider": "@pool", "model": "nested"}]},
        {"members": [{"provider": "a-b", "model": "m"}, {"provider": "A_B", "model": "m"}]},
        {"members": [{"provider": "a", "model": "m"}], "retry_count": 6},
        {"members": [{"provider": "a", "model": "m"}], "cooldown_seconds": 0},
    ]:
        with pytest.raises(ValidationError):
            ModelPoolConfig.model_validate(config)
    outer = RuntimeError("Wrapped provider error")
    outer.__cause__ = api_error(429, "insufficient_quota")
    assert classify_pool_failure(outer).code == "quota"
    assert classify_pool_failure(TimeoutError()).retryable
    assert classify_pool_failure(ConnectionError()).retryable
    assert classify_pool_failure(api_error(500)).retryable
    assert classify_pool_failure(RuntimeError("Application error")) is None
    assert classify_pool_failure(ValueError({"code": "insufficient_quota"})).code == "quota"
    assert classify_pool_failure(ValueError("ResponseError(code='server_error', message='Try later')")).retryable
    assert classify_pool_failure(ValueError("Application server_error")) is None
    assert not OutputGuard().started


@pytest.fixture
def pool_client(pool_setup):
    from agent.delivery.http.dashboard.router import router
    from agent.modules.admin_auth import get_current_admin

    container = AppContainer(config_service=pool_setup.config)
    container._model_pool_service = pool_setup.service
    container._agent_catalog_service = SimpleNamespace(list_agent_cards=lambda: [])
    previous = container.activate()
    app = FastAPI()
    app.state.container = container
    app.state.config_service = pool_setup.config
    app.include_router(router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    try:
        with TestClient(app) as client:
            yield client, container
    finally:
        container.deactivate(previous)


def test_pool_crud_defaults_and_reference_guards(pool_client, pool_setup):
    client, container = pool_client
    body = {"name": "big", "members": [{"provider": "a", "model": "custom/name"}, {"provider": "b", "model": "second"}]}
    assert client.post("/dashboard-api/model-pools", json=body).status_code == 201
    assert client.post("/dashboard-api/model-pools", json=body).status_code == 409
    rows = client.get("/dashboard-api/model-pools").json()["pools"]
    assert [row["name"] for row in rows] == ["big", "small"]
    assert pool_setup.config.get(POOL_PREFIX + "big")["members"][0]["model"] == "custom/name"
    update = {"members": list(reversed(body["members"])), "retry_count": 0}
    assert client.put("/dashboard-api/model-pools/big", json=update).status_code == 200
    assert pool_setup.config.get(POOL_PREFIX + "big")["members"][0]["provider"] == "b"
    assert client.post("/dashboard-api/model-pools/big/reset").status_code == 200
    assert client.delete("/dashboard-api/providers/a").status_code == 409
    container._agent_catalog_service = SimpleNamespace(list_agent_cards=lambda: [SimpleNamespace(provider="@pool", model="big", name="helper")])
    assert client.delete("/dashboard-api/model-pools/big").status_code == 409
    container._agent_catalog_service = SimpleNamespace(list_agent_cards=lambda: [])
    assert client.put("/settings/llm.default_model", json={"value": "@pool/big"}).status_code == 200
    assert client.delete("/dashboard-api/model-pools/big").status_code == 409
    pool_setup.source.values["llm.default_model"] = "a/first"
    assert client.delete("/dashboard-api/model-pools/big").status_code == 200
    assert client.delete("/dashboard-api/model-pools/big").status_code == 404


def test_api_validation_and_generic_settings_cannot_bypass_guards(pool_client):
    client, _ = pool_client
    base = {"name": "new", "members": [{"provider": "a", "model": "m"}]}
    assert client.post("/dashboard-api/model-pools", json={**base, "name": "Bad.Name"}).status_code == 422
    assert client.post("/dashboard-api/model-pools", json={**base, "members": []}).status_code == 422
    assert client.post("/dashboard-api/model-pools", json={**base, "members": [{"provider": "unknown", "model": "m"}]}).status_code == 400
    assert client.put("/dashboard-api/model-pools/small", json={**base}).status_code == 422
    assert client.put("/settings/llm.model_pools.small", json={"value": {"members": []}}).status_code == 400
    assert client.put("/settings", json={"values": {"llm.model_pools.small": None}}).status_code == 400
    assert client.put("/settings/llm.default_model", json={"value": "@pool/missing"}).status_code == 400


def test_pool_default_resolves_concrete_members_without_global_fallback(pool_setup):
    from agent.modules.providers.repository import ConfigProviderRepository
    from agent.modules.providers.service import ProviderService
    from agent.modules.providers.resolve_chat_model import resolve_chat_model_info

    container = AppContainer(config_service=pool_setup.config)
    previous = container.activate()
    pool_setup.source.values["llm.default_model"] = "@pool/small"
    try:
        providers = ProviderService(ConfigProviderRepository())
        assert providers.get_provider("b").name == "b"
        container._provider_service = providers
        container._model_pool_service = pool_setup.service
        assert resolve_chat_model_info(providers).pool_name == "small"
    finally:
        container.deactivate(previous)


def test_pool_effort_validation():
    from agent.delivery.http.api.schemas import ChatRequest, EditChatRequest
    from agent.modules.agents.models import AgentConfig

    assert ChatRequest(message="hi", provider="@pool", model="small", reasoning_effort="auto")
    with pytest.raises(ValidationError, match="automatic reasoning"):
        ChatRequest(message="hi", provider="@pool", model="small", reasoning_effort="high")
    with pytest.raises(ValidationError, match="automatic reasoning"):
        EditChatRequest(message="hi", thread_id="t", message_index=0, source_checkpoint_id="c",
                        provider="@pool", model="small", reasoning_effort="high")
    with pytest.raises(ValidationError, match="automatic reasoning"):
        AgentConfig(name="helper", graph_type="react_agent", provider="@pool", model="small", reasoning_effort="high")


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_real_langgraph_stream_preserves_output_boundary_and_actual_usage(pool_setup, monkeypatch, partial):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.graph import MessagesState, StateGraph, START, END
    from agent.modules.providers import get_resolved_chat_model, execute_model_call
    from agent.modules.providers.repository import ConfigProviderRepository
    from agent.modules.providers.service import ProviderService
    from agent.modules.providers.resolve_chat_model import _get_cached_model
    from agent.modules.usage import with_usage_tracking
    import agent.modules.usage.tracking as tracking

    calls, created, events = [], [], []
    monkeypatch.setattr(tracking, "_schedule_record", events.append)

    class StreamingModel(BaseChatModel):
        provider_id: str

        @property
        def _llm_type(self):
            return "pool-test"

        def _generate(self, messages, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="complete"))])

        async def _astream(self, messages, **kwargs):
            calls.append(self.provider_id)
            if self.provider_id == "a":
                if partial:
                    yield ChatGenerationChunk(message=AIMessageChunk(content="partial"))
                raise api_error(402)
            yield ChatGenerationChunk(message=AIMessageChunk(content="complete", usage_metadata={
                "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
            }))

    class Factory:
        def create(self, provider, model, key):
            created.append((provider.name, model.disable_retries))
            return StreamingModel(provider_id=provider.name)

    container = AppContainer(config_service=pool_setup.config)
    previous = container.activate()
    _get_cached_model.cache_clear()
    try:
        providers = ProviderService(ConfigProviderRepository())
        providers.register_factory(ProviderType.OPENAI_COMPATIBLE, Factory())
        providers.register_factory(ProviderType.GOOGLE, Factory())
        container._provider_service = providers
        container._model_pool_service = ModelPoolService(pool_setup.config, providers)

        async def node(state, config):
            resolved = get_resolved_chat_model(provider_name="@pool", model="small")

            async def attempt(candidate):
                response = await candidate.model.ainvoke(state["messages"], config=with_usage_tracking(
                    config, agent_name="default", provider_name=candidate.provider_name, model_name=candidate.model_name,
                ))
                return {"messages": [response]}

            return await execute_model_call(resolved, attempt)

        builder = StateGraph(MessagesState)
        builder.add_node("llm", node)
        builder.add_edge(START, "llm")
        builder.add_edge("llm", END)
        graph = builder.compile()
        output = []

        async def consume():
            async for chunk, metadata in graph.astream({"messages": [HumanMessage(content="hi")]}, stream_mode="messages"):
                output.append(chunk.content)

        if partial:
            with pytest.raises(httpx.HTTPStatusError):
                await consume()
            assert calls == ["a"]
            assert "partial" in output
        else:
            await consume()
            assert calls == ["a", "b"]
            assert "complete" in output
            assert len(events) == 1
            assert events[0].provider_name == "b"
            assert events[0].model_name == "second"
            assert events[0].usage_metadata["pool_name"] == "small"
        assert all(disabled for _, disabled in created)
        get_resolved_chat_model(provider_name="a", model="first")
        assert created[-1] == ("a", False)
    finally:
        container.deactivate(previous)
        _get_cached_model.cache_clear()


def test_pool_configuration_persists_in_existing_runtime_settings(tmp_path):
    from agent.shared.config.database_source import DatabaseConfigSource
    from agent.shared.infrastructure.db.runtime_settings import RuntimeSetting

    url = f"sqlite:///{(tmp_path / 'settings.db').as_posix()}"
    source = DatabaseConfigSource(url, key_path=tmp_path / "key")
    RuntimeSetting.__table__.create(source._engine, checkfirst=True)
    value = ModelPoolConfig(members=[{"provider": "a", "model": "first"}]).model_dump(mode="json")
    try:
        source.update_setting(POOL_PREFIX + "small", value)
        source.reload()
        assert source.get(POOL_PREFIX + "small") == value
        reopened = DatabaseConfigSource(url, key_path=tmp_path / "key")
        try:
            assert reopened.get(POOL_PREFIX + "small") == value
        finally:
            reopened.close()
    finally:
        source.close()


@pytest.mark.asyncio
async def test_opencode_pool_client_disables_sdk_and_adapter_retries():
    module = pytest.importorskip("agent.internal.providers.opencode_zen.chat_model")
    ChatOpenCodeZen = module.ChatOpenCodeZen
    from agent.internal.providers.opencode_zen.factory import OpenCodeZenFactory
    from agent.modules.providers.models import ModelConfig

    provider = ProviderConfig(name="opencode", provider_type=ProviderType.OPENAI_COMPATIBLE,
                              base_url="https://example.com/v1", api_key="public", default_model="test-model")
    configured = OpenCodeZenFactory().create(provider, ModelConfig(model_name="test-model", disable_retries=True), "public")
    assert configured.max_retries == 0
    assert configured.pool_managed_retries
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"type": "FreeUsageLimitError", "message": "Rate limit exceeded."}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model = ChatOpenCodeZen(model="test-model", api_key="public", http_async_client=client,
                                max_retries=0, pool_managed_retries=True, cache=False)
        with pytest.raises(RuntimeError):
            await model.ainvoke("hi")
    assert len(calls) == 1
