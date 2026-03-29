"""Tests for the new providers module (Phase 4)."""

import pytest
from pytest import MonkeyPatch
from unittest.mock import MagicMock

from agent.modules.providers.domain.provider import ProviderConfig, ProviderType
from agent.modules.providers.domain.model import ModelConfig
from agent.modules.providers.domain.ports import ChatModelFactory
from agent.modules.providers.application.provider_service import ProviderService
from agent.modules.providers.application.resolve_chat_model import (
    _parse_temperature,
    resolve_chat_model,
    _get_cached_model,
)
from agent.modules.providers.infrastructure.repository import (
    EnvProviderRepository,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
)


# --- Domain ---


def test_provider_config_creation():
    config = ProviderConfig(
        name="test",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.test.com/v1",
        api_key_env_var="TEST_API_KEY",
        default_model="test-model",
    )
    assert config.name == "test"
    assert config.provider_type == ProviderType.OPENAI_COMPATIBLE
    assert config.enabled is True


def test_model_config_defaults():
    mc = ModelConfig(model_name="gpt-4")
    assert mc.temperature == 0.0
    assert mc.max_tokens is None


# --- Infrastructure: EnvProviderRepository ---


def test_env_repo_default_provider(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    repo = EnvProviderRepository()
    provider = repo.get_default_provider()

    assert provider.name == "default"
    assert provider.provider_type == ProviderType.OPENAI_COMPATIBLE
    assert provider.base_url == DEFAULT_BASE_URL
    assert provider.default_model == DEFAULT_MODEL
    assert provider.api_key_env_var == "LLM_API_KEY"


def test_env_repo_falls_back_to_openai_key(monkeypatch: MonkeyPatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    repo = EnvProviderRepository()
    provider = repo.get_default_provider()

    assert provider.api_key_env_var == "OPENAI_API_KEY"


def test_env_repo_custom_base_url(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://custom.api/v1")

    repo = EnvProviderRepository()
    provider = repo.get_default_provider()

    assert provider.base_url == "https://custom.api/v1"


def test_env_repo_unknown_provider_raises():
    repo = EnvProviderRepository()
    with pytest.raises(KeyError, match="not-found"):
        repo.get_provider("not-found")


def test_env_repo_list_providers(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    repo = EnvProviderRepository()
    providers = repo.list_providers()

    assert len(providers) == 1
    assert providers[0].name == "default"


# --- Application: ProviderService ---


def test_provider_service_factory_registration():
    repo = EnvProviderRepository()
    service = ProviderService(repository=repo)

    mock_factory = MagicMock(spec=ChatModelFactory)
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    assert service.get_factory(ProviderType.OPENAI_COMPATIBLE) is mock_factory


def test_provider_service_missing_factory_raises():
    repo = EnvProviderRepository()
    service = ProviderService(repository=repo)

    with pytest.raises(RuntimeError, match="No factory registered"):
        service.get_factory(ProviderType.ANTHROPIC)


# --- Application: resolve_chat_model ---


def test_resolve_chat_model_with_mock_factory(monkeypatch: MonkeyPatch):
    # Clear cache to avoid cross-test interference
    _get_cached_model.cache_clear()

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    repo = EnvProviderRepository()
    service = ProviderService(repository=repo)

    mock_model = MagicMock()
    mock_factory = MagicMock()
    mock_factory.create.return_value = mock_model

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    result = resolve_chat_model(service)

    assert result is mock_model
    mock_factory.create.assert_called_once()

    _get_cached_model.cache_clear()


def test_resolve_chat_model_missing_api_key_raises(monkeypatch: MonkeyPatch):
    _get_cached_model.cache_clear()

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    repo = EnvProviderRepository()
    service = ProviderService(repository=repo)

    mock_factory = MagicMock()
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    with pytest.raises(RuntimeError, match="API key not configured"):
        resolve_chat_model(service)

    _get_cached_model.cache_clear()


# --- Application: _parse_temperature ---


def test_parse_temperature_none():
    assert _parse_temperature(None, 0.5) == 0.5


def test_parse_temperature_empty():
    assert _parse_temperature("", 0.5) == 0.5


def test_parse_temperature_valid():
    assert _parse_temperature("0.7", 0.5) == 0.7


def test_parse_temperature_invalid():
    with pytest.raises(ValueError, match="LLM_TEMPERATURE"):
        _parse_temperature("not-a-number", 0.5)


# --- Public: backward compat shim ---


def test_backward_compat_get_llm_import():
    """Verify the old import path still works."""
    from agent.providers import get_llm  # noqa: F401

    assert callable(get_llm)


def test_backward_compat_llm_module_import():
    """Verify the old agent.providers.llm path still works."""
    from agent.providers.llm import (  # noqa: F401
        DEFAULT_LLM_BASE_URL,
        DEFAULT_LLM_MODEL,
        LLMSettings,
        _resolve_llm_settings,
        get_llm,
    )

    assert callable(get_llm)
    assert callable(_resolve_llm_settings)


# --- Public: get_chat_model integration ---


def test_get_chat_model_import():
    from agent.modules.providers.public import get_chat_model  # noqa: F401

    assert callable(get_chat_model)
