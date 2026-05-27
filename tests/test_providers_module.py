"""Tests for the providers module with YAML-only configuration."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest import MonkeyPatch

from agent.modules.providers.service import ProviderService
from agent.modules.providers.resolve_chat_model import (
    _get_cached_model,
    _parse_temperature,
    resolve_chat_model,
)
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.ports import ChatModelFactory
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.providers.repository import (
    ConfigProviderRepository,
    DEFAULT_BASE_URL,
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
    provider_name: str = "openai-main",
    provider_type: str = "openai_compatible",
    default_provider: str | None = None,
    api_key: str = "test-key",
    base_url: str = DEFAULT_BASE_URL,
    default_model: str = "test-model",
    temperature: float | None = None,
) -> None:
    resolved_default_provider = default_provider or provider_name
    llm_block = [
        "llm:",
        f"  default_provider: {resolved_default_provider!r}",
        "  providers:",
        f"    {provider_name}:",
        f"      type: {provider_type!r}",
        f"      api_key: {api_key!r}",
        f"      default_model: {default_model!r}",
    ]
    if base_url:
        llm_block.append(f"      base_url: {base_url!r}")
    if temperature is not None:
        llm_block.append(f"      temperature: {temperature}")

    config_path.write_text("\n".join(llm_block) + "\n", encoding="utf-8")


def _write_yaml(config_path: Path, content: str) -> None:
    config_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _bump_mtime(config_path: Path) -> None:
    stat = config_path.stat()
    os.utime(
        config_path,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
    )


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

    assert provider.name == "openai-main"
    assert provider.provider_type == ProviderType.OPENAI_COMPATIBLE
    assert provider.base_url == DEFAULT_BASE_URL
    assert provider.default_model == "test-model"
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
        provider_name="google-main",
        provider_type="google",
        default_provider="google-main",
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


def test_repo_anthropic_provider_from_yaml(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        provider_name="anthropic-main",
        provider_type="anthropic",
        default_provider="anthropic-main",
        api_key="anthropic-key",
        base_url="https://ignored.example/v1",
        default_model="claude-model",
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_default_provider()

    assert provider.provider_type == ProviderType.ANTHROPIC
    assert provider.default_model == "claude-model"
    assert provider.base_url == ""
    assert provider.api_key == "anthropic-key"

    by_alias = repo.get_provider("anthropic")
    assert by_alias.provider_type == ProviderType.ANTHROPIC


def test_repo_multi_provider_with_default_provider(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "google-main"
                    providers:
                        openai-main:
                            type: "openai_compatible"
                            api_key: "openai-key"
                            base_url: "https://openai-compatible.local/v1"
                            default_model: "openai-main-model"
                        google-main:
                            type: "google"
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


def test_repo_provider_models_from_yaml(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "openai-main"
                    providers:
                        openai-main:
                            type: "openai_compatible"
                            api_key: "openai-key"
                            default_model: "openai-default"
                            models:
                                - "openai-default"
                                - "openai-fast"
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()
        provider = repo.get_provider("openai-main")

        assert provider.models == ("openai-default", "openai-fast")


def test_repo_detects_provider_config_file_changes_without_manual_reload(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_provider: "openai-main"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "openai-key"
                    default_model: "openai-default"
                    models:
                        - "openai-default"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()

    assert repo.get_provider("openai-main").models == ("openai-default",)

    _write_yaml(
        config_path,
        """
        llm:
            default_provider: "openai-main"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "openai-key"
                    default_model: "openai-default"
                    models:
                        - "openai-default"
                        - "openai-fast"
        """,
    )
    _bump_mtime(config_path)

    assert repo.get_provider("openai-main").models == (
        "openai-default",
        "openai-fast",
    )


def test_repo_requires_provider_specific_default_model(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "openai-main"
                    default_model: "global-model"
                    providers:
                        openai-main:
                            type: "openai_compatible"
                            api_key: "openai-key"
                        google-main:
                            type: "google"
                            api_key: "google-key"
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()

        assert repo.get_provider("openai-main").default_model == ""
        assert repo.get_provider("google-main").default_model == ""


def test_repo_non_default_provider_can_omit_default_model(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "google-main"
                    default_model: "global-model"
                    providers:
                        openai-main:
                            type: "openai_compatible"
                            api_key: "openai-key"
                        google-main:
                            type: "google"
                            api_key: "google-key"
                            default_model: "google-model"
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()

        assert repo.get_provider("openai-main").default_model == ""
        assert repo.get_provider("google-main").default_model == "google-model"


def test_repo_global_default_model_does_not_override_provider_default_model(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "google-main"
                    default_model: "global-model"
                    providers:
                        openai-main:
                            type: "openai_compatible"
                            api_key: "openai-key"
                            default_model: "openai-provider-model"
                        google-main:
                            type: "google"
                            api_key: "google-key"
                            default_model: "google-provider-model"
                """,
        )
        _set_config_path(monkeypatch, config_path)

        repo = ConfigProviderRepository()

        assert repo.get_provider("openai-main").default_model == "openai-provider-model"
        assert repo.get_provider("google-main").default_model == "google-provider-model"


def test_repo_default_provider_cannot_be_disabled(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_yaml(
                config_path,
                """
                llm:
                    default_provider: "google-main"
                    providers:
                        google-main:
                            type: "google"
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
    _write_yaml(
        config_path,
        """
        llm:
            default_provider: "openai-main"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: ""
                    default_model: "model"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    with pytest.raises(RuntimeError, match="llm.providers.openai-main.api_key"):
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
    assert providers[0].name == "openai-main"


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


@pytest.mark.asyncio
async def test_provider_service_model_catalog_merges_live_config_and_default(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_provider: "openai-main"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "openai-key"
                    default_model: "openai-default"
                    models:
                        - "openai-fast"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)
    class ListingFactory:
        def create(self, provider_config, model_config, api_key):
            raise NotImplementedError

        def list_models(self, provider_config, api_key):
            return ["openai-live", "openai-fast"]

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, ListingFactory())

    catalog = await service.list_model_catalog("openai-main", include_remote=True)

    assert catalog.provider == "openai-main"
    assert catalog.default_model == "openai-default"
    assert catalog.can_list_models is True
    assert [(option.id, option.source) for option in catalog.models] == [
        ("openai-live", "live"),
        ("openai-fast", "live"),
        ("openai-default", "default"),
    ]


@pytest.mark.asyncio
async def test_provider_service_model_catalog_uses_config_when_factory_cannot_list(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_provider: "openai-main"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "openai-key"
                    default_model: "openai-default"
                    models:
                        - "openai-fast"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)
    class ConfigOnlyFactory:
        def create(self, provider_config, model_config, api_key):
            raise NotImplementedError

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, ConfigOnlyFactory())

    catalog = await service.list_model_catalog("openai-main", include_remote=True)

    assert catalog.can_list_models is False
    assert [(option.id, option.source) for option in catalog.models] == [
        ("openai-fast", "config"),
        ("openai-default", "default"),
    ]


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


def test_resolve_chat_model_prefers_explicit_model(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_config(config_path, api_key="model-key", default_model="config-model")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    mock_model = MagicMock()
    mock_factory = MagicMock()
    mock_factory.create.return_value = mock_model

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    result = resolve_chat_model(service, model="direct-model")

    assert result is mock_model
    call_args = mock_factory.create.call_args.args
    model_config = call_args[1]
    assert model_config.model_name == "direct-model"

    _get_cached_model.cache_clear()


def test_resolve_chat_model_missing_api_key_raises(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_provider: "openai-main"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: ""
                    default_model: "model"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    mock_factory = MagicMock()
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    with pytest.raises(RuntimeError, match="llm.providers.openai-main.api_key"):
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


def test_resolve_chat_model_applies_provider_yaml_updates_immediately(
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
    config_service.update_setting("llm.providers.openai-main.api_key", "key-two")
    config_service.update_setting(
        "llm.providers.openai-main.base_url",
        "https://api.two/v1",
    )

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
        provider_name="google-main",
        provider_type="google",
        default_provider="google-main",
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


def test_resolve_chat_model_uses_anthropic_factory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        provider_name="anthropic-main",
        provider_type="anthropic",
        default_provider="anthropic-main",
        api_key="anthropic-key",
        default_model="claude-model",
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    openai_factory = MagicMock()
    anthropic_factory = MagicMock()
    anthropic_model = MagicMock()
    anthropic_factory.create.return_value = anthropic_model

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, openai_factory)
    service.register_factory(ProviderType.ANTHROPIC, anthropic_factory)

    result = resolve_chat_model(service)

    assert result is anthropic_model
    anthropic_factory.create.assert_called_once()
    openai_factory.create.assert_not_called()

    call_args = anthropic_factory.create.call_args.args
    assert call_args[0].provider_type == ProviderType.ANTHROPIC
    assert call_args[2] == "anthropic-key"

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
                    providers:
                        openai-main:
                            type: "openai_compatible"
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
    with pytest.raises(ValueError, match="llm.providers.<provider>.temperature"):
        _parse_temperature("not-a-number", 0.5)


# --- Public API import ---


def test_get_chat_model_import() -> None:
    from agent.modules.providers import get_chat_model  # noqa: F401

    assert callable(get_chat_model)
