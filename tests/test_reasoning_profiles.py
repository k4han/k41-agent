"""Model-specific effort discovery, overrides, and provider request mapping."""

import json
from importlib import import_module
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from agent.delivery.http.dashboard.routes.helpers.providers import serialize_model_catalog
from agent.delivery.http.dashboard.routes.helpers.settings import normalize_setting_value
from agent.modules.providers.models import get_reasoning_effort_kwargs
from agent.modules.providers.profiles import get_model_profile, parse_model_profiles, reasoning_metadata
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.providers.resolve_chat_model import resolve_chat_model_info, _get_cached_model
from agent.modules.providers.service import ProviderService
from agent.shared.config.constants import is_database_runtime_key, get_setting_metadata


@pytest.mark.parametrize("provider,model,level", [
    ("openai_compatible", "gpt-5", "minimal"),
    ("openai", "gpt-5.2", "xhigh"),
    ("anthropic", "claude-opus-4-6", "max"),
    ("google", "gemini-3-flash-preview", "minimal"),
])
def test_installed_profiles_supply_model_specific_levels(provider, model, level):
    profile = get_model_profile(provider, model)
    levels, _ = reasoning_metadata(profile)
    assert level in levels
    assert get_reasoning_effort_kwargs(provider, model, level, profile=profile) == {"reasoning_effort": level}


def test_missing_metadata_is_distinct_from_unsupported():
    assert reasoning_metadata(get_model_profile("openai", "unknown-model")) == (None, None)
    assert reasoning_metadata(get_model_profile("openai", "gpt-4.1")) == ((), None)
    assert reasoning_metadata(get_model_profile("google", "gemini-2.5-pro")) == (None, None)
    assert get_reasoning_effort_kwargs("openai", "unknown-model", "high") == {}


def test_namespaced_model_lookup_does_not_guess_arbitrary_aliases():
    assert reasoning_metadata(get_model_profile("openai_compatible", "openai/gpt-5"))[0]
    assert reasoning_metadata(get_model_profile("openai_compatible", "custom/gpt-5"))[0] is None


@pytest.mark.parametrize("model_name", ["gpt-6.1-sol", "openai/gpt-6.1-sol"])
@pytest.mark.parametrize("profile", [{}, {"reasoning_effort_levels": []}])
def test_gpt6_tool_requests_use_responses_without_effort_metadata(model_name, profile):
    from langchain_core.messages import HumanMessage
    from agent.modules.providers.models import ModelConfig
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

    provider = ProviderConfig(
        name="test", provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://gateway.example/v1", api_key="test-key", default_model=model_name,
    )
    model = OpenAICompatibleFactory().create(
        provider, ModelConfig(model_name=model_name, profile=profile), "test-key",
    )
    tool = {
        "type": "function",
        "function": {
            "name": "echo", "description": "Return the input text.",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    }
    bound = model.bind_tools(
        [tool], **get_reasoning_effort_kwargs("openai_compatible", model_name, None, profile=profile),
    )
    payload = model._get_request_payload([HumanMessage(content="Hello")], **bound.kwargs)
    assert "input" in payload
    assert "messages" not in payload
    assert "reasoning" not in payload
    assert payload["tools"][0]["name"] == "echo"


def test_default_effort_and_explicit_override():
    assert get_reasoning_effort_kwargs("anthropic", "claude-opus-4-6", None) == {"reasoning_effort": "high"}
    assert get_reasoning_effort_kwargs("anthropic", "claude-opus-4-6", "low") == {"reasoning_effort": "low"}
    assert get_reasoning_effort_kwargs("openai", "gpt-5", None) == {}
    with pytest.raises(ValueError, match="Unsupported reasoning effort"):
        get_reasoning_effort_kwargs("openai", "gpt-5", "max")


def test_overrides_can_enable_disable_and_replace_metadata():
    overrides = parse_model_profiles({
        "gpt-6.1-sol": {"reasoning_effort_levels": ["low", "high"], "reasoning_effort_default": "high"},
        "claude-opus-4-6": {"reasoning_effort_levels": ["low"]},
        "gpt-5": {"reasoning_effort_levels": []},
    })
    profile = get_model_profile("openai", "gpt-6.1-sol", overrides)
    assert get_reasoning_effort_kwargs("openai", "gpt-6.1-sol", None, profile=profile) == {"reasoning": {"effort": "high"}}
    assert reasoning_metadata(get_model_profile("anthropic", "claude-opus-4-6", overrides)) == (("low",), None)
    assert get_reasoning_effort_kwargs("openai", "gpt-5", "high", profile=get_model_profile("openai", "gpt-5", overrides)) == {}
    assert reasoning_metadata(get_model_profile("openai", "gpt-5"))[0]


@pytest.mark.parametrize("value", [
    "invalid json", "[]", {"custom": []}, {"": {}},
    {"custom": {"unknown_field": True}},
    {"custom": {"reasoning_effort_levels": "high"}},
    {"custom": {"reasoning_effort_levels": ["high", "high"]}},
    {"custom": {"reasoning_effort_levels": ["High"]}},
    {"custom": {"reasoning_effort_levels": ["low"], "reasoning_effort_default": "high"}},
])
def test_settings_reject_invalid_profiles_before_saving(value):
    with pytest.raises(HTTPException) as error:
        normalize_setting_value("llm.providers.custom.model_profiles", value)
    assert error.value.status_code == 400


def test_model_profile_setting_accepts_object_or_json_and_can_be_cleared():
    key = "llm.providers.custom.model_profiles"
    profiles = {"custom-model": {"reasoning_effort_levels": ["low", "high"], "reasoning_effort_default": "low"}}
    assert is_database_runtime_key(key)
    assert get_setting_metadata(key)["label"].endswith("Model Reasoning Profiles (JSON)")
    assert normalize_setting_value(key, profiles) == profiles
    assert normalize_setting_value(key, json.dumps(profiles)) == profiles
    assert normalize_setting_value(key, "") == {}


@pytest.mark.asyncio
async def test_catalog_and_runtime_share_profiles_and_refresh_overrides(monkeypatch):
    resolver = import_module("agent.modules.providers.resolve_chat_model")

    provider = ProviderConfig(
        name="custom", provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="", api_key="test-key", default_model="custom-model", models=("custom-model",),
        model_profiles={"custom-model": {"reasoning_effort_levels": ["low", "high"], "reasoning_effort_default": "high"}},
    )
    repo = SimpleNamespace(get_provider=lambda name: provider, get_default_provider=lambda: provider)
    service = ProviderService(repo)
    created = []

    class Factory:
        def create(self, provider_config, model_config, api_key):
            created.append(model_config)
            return SimpleNamespace(profile=model_config.profile)

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, Factory())
    monkeypatch.setattr(resolver, "get_default_llm_settings", lambda: ("custom", "custom-model"))
    monkeypatch.setattr(resolver, "get_config_service", lambda: SimpleNamespace(get=lambda key: None))
    _get_cached_model.cache_clear()
    try:
        catalog = await service.list_model_catalog("custom")
        option = serialize_model_catalog(catalog)["models"][0]
        assert option["reasoning_effort_levels"] == ["low", "high"]
        assert option["reasoning_effort_default"] == "high"
        resolved = resolve_chat_model_info(service, provider_name="custom")
        assert reasoning_metadata(resolved.profile) == (("low", "high"), "high")
        assert created[-1].profile == resolved.profile
        assert resolve_chat_model_info(service, provider_name="custom").model is resolved.model
        provider.model_profiles["custom-model"]["reasoning_effort_default"] = "low"
        updated = resolve_chat_model_info(service, provider_name="custom")
        assert updated.model is not resolved.model
        assert get_reasoning_effort_kwargs(updated.provider_type, updated.model_name, None, profile=updated.profile) == {"reasoning_effort": "low"}
        monkeypatch.setattr(resolver, "get_default_llm_settings", lambda: ("custom", "global-model"))
        assert resolve_chat_model_info(service, provider_name="custom").model_name == "custom-model"
        assert resolve_chat_model_info(service).model_name == "global-model"
        provider.model_profiles["new-model"] = {"reasoning_effort_levels": ["low"]}
        assert "new-model" in {option.id for option in (await service.list_model_catalog("custom")).models}
    finally:
        _get_cached_model.cache_clear()


@pytest.mark.parametrize("provider_type,model_name,effort", [
    (ProviderType.OPENAI_COMPATIBLE, "gpt-5", "minimal"),
    (ProviderType.ANTHROPIC, "claude-opus-4-6", "max"),
    (ProviderType.ANTHROPIC, "claude-opus-5-5", "low"),
    (ProviderType.GOOGLE, "gemini-3-flash-preview", "minimal"),
])
def test_real_factories_bind_effort_into_native_request(provider_type, model_name, effort):
    from langchain_core.messages import HumanMessage
    from agent.modules.providers.anthropic.factory import AnthropicFactory
    from agent.modules.providers.google.factory import GoogleFactory
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory
    from agent.modules.providers.models import ModelConfig

    factory = {
        ProviderType.OPENAI_COMPATIBLE: OpenAICompatibleFactory,
        ProviderType.ANTHROPIC: AnthropicFactory,
        ProviderType.GOOGLE: GoogleFactory,
    }[provider_type]()
    profile = get_model_profile(str(provider_type), model_name)
    provider = ProviderConfig(name="test", provider_type=provider_type, base_url="", api_key="test-key", default_model=model_name)
    model = factory.create(provider, ModelConfig(model_name=model_name, profile=profile), "test-key")
    bound = model.bind_tools([], **get_reasoning_effort_kwargs(str(provider_type), model_name, effort, profile=profile))
    if provider_type == ProviderType.GOOGLE:
        params = model._prepare_params(None, **bound.kwargs)
        assert params.thinking_config.thinking_level.value.lower() == effort
    else:
        payload = model._get_request_payload([HumanMessage(content="Hello")], **bound.kwargs)
        if provider_type == ProviderType.ANTHROPIC:
            assert payload["output_config"]["effort"] == effort
            assert "reasoning_effort" not in payload
        else:
            assert payload.get("reasoning_effort", payload.get("reasoning", {}).get("effort")) == effort
