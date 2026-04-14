"""Tests for the providers module with YAML-only configuration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest import MonkeyPatch

from agent.modules.providers.application.provider_service import ProviderService
from agent.modules.providers.application.resolve_chat_model import (
    _get_cached_model,
    _parse_temperature,
    resolve_chat_model,
)
from agent.modules.providers.domain.model import ModelConfig
from agent.modules.providers.domain.ports import ChatModelFactory
from agent.modules.providers.domain.provider import ProviderConfig, ProviderType
from agent.modules.providers.infrastructure.repository import (
    ConfigProviderRepository,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
)


def _set_config_path(monkeypatch: MonkeyPatch, config_path: Path) -> None:
    import agent.shared.config.service as service_module
    import agent.shared.config.yaml_source as yaml_module

    monkeypatch.setattr(service_module, "_config_service", None)
    monkeypatch.setattr(service_module, "_config_sources", None)
    monkeypatch.setattr(yaml_module, "DEFAULT_CONFIG_PATH", config_path)


def _write_config(
    config_path: Path,
    *,
    api_key: str = "test-key",
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
) -> None:
    llm_block = [
        "llm:",
        f"  api_key: {api_key!r}",
        f"  base_url: {base_url!r}",
        f"  model: {model!r}",
    ]
    if temperature is not None:
        llm_block.append(f"  temperature: {temperature}")

    config_path.write_text("\n".join(llm_block) + "\n", encoding="utf-8")


# --- Domain ---


def test_provider_config_creation() -> None:
    config = ProviderConfig(
        name="test",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.test.com/v1",
        api_key="test-key",
        default_model="test-model",
    )
    assert config.name == "test"
    assert config.provider_type == ProviderType.OPENAI_COMPATIBLE
    assert config.enabled is True


def test_model_config_defaults() -> None:
    mc = ModelConfig(model_name="gpt-4")
    assert mc.temperature == 0.0
    assert mc.max_tokens is None


# --- Infrastructure: ConfigProviderRepository ---


def test_repo_default_provider_from_yaml(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, api_key="repo-key")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_default_provider()

    assert provider.name == "default"
    assert provider.provider_type == ProviderType.OPENAI_COMPATIBLE
    assert provider.base_url == DEFAULT_BASE_URL
    assert provider.default_model == DEFAULT_MODEL
    assert provider.api_key == "repo-key"


def test_repo_custom_base_url_and_model(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        api_key="repo-key",
        base_url="https://custom.api/v1",
        model="custom-model",
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_default_provider()

    assert provider.base_url == "https://custom.api/v1"
    assert provider.default_model == "custom-model"


def test_repo_missing_api_key_raises(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  api_key: ''\n", encoding="utf-8")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    with pytest.raises(RuntimeError, match="llm.api_key"):
        repo.get_default_provider()


def test_repo_unknown_provider_raises(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    with pytest.raises(KeyError, match="not-found"):
        repo.get_provider("not-found")


def test_repo_list_providers(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    providers = repo.list_providers()

    assert len(providers) == 1
    assert providers[0].name == "default"


# --- Application: ProviderService ---


def test_provider_service_factory_registration(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    mock_factory = MagicMock(spec=ChatModelFactory)
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    assert service.get_factory(ProviderType.OPENAI_COMPATIBLE) is mock_factory


def test_provider_service_missing_factory_raises(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    with pytest.raises(RuntimeError, match="No factory registered"):
        service.get_factory(ProviderType.ANTHROPIC)


# --- Application: resolve_chat_model ---


def test_resolve_chat_model_with_mock_factory(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_config(config_path, api_key="model-key")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    mock_model = MagicMock()
    mock_factory = MagicMock()
    mock_factory.create.return_value = mock_model

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    result = resolve_chat_model(service)

    assert result is mock_model
    mock_factory.create.assert_called_once()

    _get_cached_model.cache_clear()


def test_resolve_chat_model_missing_api_key_raises(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  api_key: ''\n", encoding="utf-8")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    mock_factory = MagicMock()
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    with pytest.raises(RuntimeError, match="llm.api_key"):
        resolve_chat_model(service)

    _get_cached_model.cache_clear()


def test_resolve_chat_model_applies_yaml_updates_immediately(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_config(config_path, api_key="key-one", base_url="https://api.one/v1")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    model_one = MagicMock(name="model_one")
    model_two = MagicMock(name="model_two")
    mock_factory = MagicMock()
    mock_factory.create.side_effect = [model_one, model_two]
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    first = resolve_chat_model(service)

    from agent.shared.config import get_config_service

    config_service = get_config_service()
    config_service.update_setting("llm.api_key", "key-two")
    config_service.update_setting("llm.base_url", "https://api.two/v1")

    second = resolve_chat_model(service)

    assert first is model_one
    assert second is model_two
    assert mock_factory.create.call_count == 2

    first_call = mock_factory.create.call_args_list[0].args
    second_call = mock_factory.create.call_args_list[1].args

    assert first_call[0].base_url == "https://api.one/v1"
    assert first_call[2] == "key-one"
    assert second_call[0].base_url == "https://api.two/v1"
    assert second_call[2] == "key-two"

    _get_cached_model.cache_clear()


# --- Application: _parse_temperature ---


def test_parse_temperature_none() -> None:
    assert _parse_temperature(None, 0.5) == 0.5


def test_parse_temperature_empty() -> None:
    assert _parse_temperature("", 0.5) == 0.5


def test_parse_temperature_valid() -> None:
    assert _parse_temperature("0.7", 0.5) == 0.7


def test_parse_temperature_invalid() -> None:
    with pytest.raises(ValueError, match="llm.temperature"):
        _parse_temperature("not-a-number", 0.5)


# --- Public API import ---


def test_get_chat_model_import() -> None:
    from agent.modules.providers.public import get_chat_model  # noqa: F401

    assert callable(get_chat_model)
