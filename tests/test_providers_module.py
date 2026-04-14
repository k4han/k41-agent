"""Tests for the providers module with YAML-only configuration."""

from __future__ import annotations

import textwrap
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
    DEFAULT_PROVIDER,
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
    default_provider: str = DEFAULT_PROVIDER,
    api_key: str = "test-key",
    base_url: str = DEFAULT_BASE_URL,
    default_model: str = DEFAULT_MODEL,
    temperature: float | None = None,
) -> None:
    llm_block = [
        "llm:",
        f"  default_provider: {default_provider!r}",
        f"  api_key: {api_key!r}",
        f"  base_url: {base_url!r}",
        f"  default_model: {default_model!r}",
    ]
    if temperature is not None:
        llm_block.append(f"  temperature: {temperature}")

    config_path.write_text("\n".join(llm_block) + "\n", encoding="utf-8")


def _write_yaml(config_path: Path, content: str) -> None:
    config_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


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
    mc = ModelConfig(model_name="test-model")
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

    by_alias = repo.get_provider("openai_compatible")
    assert by_alias.provider_type == ProviderType.OPENAI_COMPATIBLE


def test_repo_custom_base_url_and_model(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        api_key="repo-key",
        base_url="https://custom.api/v1",
        default_model="custom-model",
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_default_provider()

    assert provider.base_url == "https://custom.api/v1"
    assert provider.default_model == "custom-model"


def test_repo_google_provider_from_yaml(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        default_provider="google",
        api_key="google-key",
        default_model="google-model",
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_default_provider()

    assert provider.provider_type == ProviderType.GOOGLE
    assert provider.default_model == "google-model"
    assert provider.base_url == ""
    assert provider.api_key == "google-key"

    by_alias = repo.get_provider("google")
    assert by_alias.provider_type == ProviderType.GOOGLE


def test_repo_multi_provider_with_default_provider(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "google-main"
                    providers:
                        openai-main:
                            provider: "openai_compatible"
                            api_key: "openai-key"
                            base_url: "https://openai-compatible.local/v1"
                            default_model: "openai-main-model"
                        google-main:
                            provider: "google"
                            api_key: "google-key"
                            default_model: "google-main-model"
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()
        default_provider = repo.get_default_provider()
        openai_provider = repo.get_provider("openai-main")
        google_provider = repo.get_provider("google")

        assert default_provider.name == "google-main"
        assert default_provider.provider_type == ProviderType.GOOGLE
        assert default_provider.default_model == "google-main-model"

        assert openai_provider.provider_type == ProviderType.OPENAI_COMPATIBLE
        assert openai_provider.base_url == "https://openai-compatible.local/v1"
        assert openai_provider.default_model == "openai-main-model"

        assert google_provider.name == "google-main"


def test_repo_multi_provider_global_default_model(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "openai-main"
                    default_model: "global-model"
                    providers:
                        openai-main:
                            provider: "openai_compatible"
                            api_key: "openai-key"
                        google-main:
                            provider: "google"
                            api_key: "google-key"
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()

        assert repo.get_provider("openai-main").default_model == "global-model"
        assert repo.get_provider("google-main").default_model == "global-model"


def test_repo_default_provider_cannot_be_disabled(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "google-main"
                    providers:
                        google-main:
                            provider: "google"
                            api_key: "google-key"
                            enabled: false
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()
        with pytest.raises(RuntimeError, match="disabled"):
                repo.get_default_provider()


def test_repo_invalid_provider_raises(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, default_provider="not-supported")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    with pytest.raises(ValueError, match="llm.default_provider"):
        repo.get_default_provider()


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
    _write_config(config_path, api_key="model-key", default_model="test-model")
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


def test_resolve_chat_model_missing_model_raises(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_config(config_path, api_key="model-key", default_model="")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    mock_factory = MagicMock()
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    with pytest.raises(RuntimeError, match="Model not configured"):
        resolve_chat_model(service)

    _get_cached_model.cache_clear()


def test_resolve_chat_model_applies_yaml_updates_immediately(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        api_key="key-one",
        base_url="https://api.one/v1",
        default_model="test-model",
    )
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

    # ConfigService.update_setting calls reload() which clears YAML cache, so the
    # YAML is re-read on next access. Manually reload the repo instance used by
    # the test so it picks up the new config values.
    repo.reload()
    _get_cached_model.cache_clear()

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


def test_resolve_chat_model_uses_google_factory(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        default_provider="google",
        api_key="google-key",
        default_model="google-model",
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    openai_factory = MagicMock()
    google_factory = MagicMock()
    google_model = MagicMock()
    google_factory.create.return_value = google_model

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, openai_factory)
    service.register_factory(ProviderType.GOOGLE, google_factory)

    result = resolve_chat_model(service)

    assert result is google_model
    google_factory.create.assert_called_once()
    openai_factory.create.assert_not_called()

    call_args = google_factory.create.call_args.args
    assert call_args[0].provider_type == ProviderType.GOOGLE
    assert call_args[2] == "google-key"

    _get_cached_model.cache_clear()


def test_resolve_chat_model_uses_provider_specific_temperature(
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
) -> None:
        _get_cached_model.cache_clear()

        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "openai-main"
                    temperature: 1.2
                    providers:
                        openai-main:
                            provider: "openai_compatible"
                            api_key: "openai-key"
                            default_model: "provider-model"
                            temperature: 0.2
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()
        service = ProviderService(repository=repo)

        mock_model = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_model

        service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

        result = resolve_chat_model(service)

        assert result is mock_model
        call_args = mock_factory.create.call_args.args
        model_config = call_args[1]
        assert model_config.temperature == 0.2

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
