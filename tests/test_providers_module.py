"""Tests for the providers module with database-owned runtime configuration."""

from __future__ import annotations

import os
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest import MonkeyPatch

import agent.modules.providers.catalog as catalog_module
from agent.modules.providers.resolve_chat_model import (
    _get_cached_model,
    _parse_temperature,
    resolve_chat_model,
)
from agent.modules.providers.service import ProviderService
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.ports import ChatModelFactory
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.providers.repository import (
    ConfigProviderRepository,
    DEFAULT_BASE_URL,
)
from agent.shared.config import ConfigService, SettingsSource, SettingsValue
from agent.shared.config.constants import is_database_runtime_key
from agent.shared.config.default_source import DefaultConfigSource
from agent.shared.infrastructure.config_file import flatten_config_mapping


def _set_config_path(monkeypatch: MonkeyPatch, config_path: Path) -> None:
    import yaml

    from agent.bootstrap.container import AppContainer, set_active_container

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    flat = flatten_config_mapping(raw)
    source = _WritableRuntimeSource(
        {
            key: SettingsValue(key=key, value=value, source=SettingsSource.DATABASE)
            for key, value in flat.items()
            if is_database_runtime_key(key) or key == "llm.default_provider"
        }
    )
    service = ConfigService(sources=[DefaultConfigSource(), source])

    set_active_container(AppContainer(config_service=service))


class _WritableRuntimeSource:
    def __init__(self, entries: dict[str, SettingsValue]) -> None:
        self._entries = dict(entries)
        self._priority = 200

    def can_update_key(self, key: str) -> bool:
        return is_database_runtime_key(key) or key == "llm.default_provider"

    def get(self, key: str) -> object | None:
        value = self._entries.get(key)
        return value.value if value else None

    def get_all(self) -> dict[str, object]:
        return {key: value.value for key, value in self._entries.items()}

    def get_settings_value(self, key: str) -> SettingsValue | None:
        return self._entries.get(key)

    def get_all_settings_values(self, keys: set[str] | None = None) -> dict[str, SettingsValue]:
        if keys is None:
            return dict(self._entries)
        return {key: value for key, value in self._entries.items() if key in keys}

    def update_setting(self, key: str, value: object) -> None:
        self.update_settings({key: value})

    def update_settings(self, updates: dict[str, object]) -> None:
        for key, value in updates.items():
            if not self.can_update_key(key):
                continue
            self._entries[key] = SettingsValue(
                key=key,
                value=value,
                source=SettingsSource.DATABASE,
            )

    def reload(self) -> None:
        pass

    @property
    def priority(self) -> int:
        return self._priority


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
        f"  default_model: {resolved_default_provider}/{default_model}",
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
                    default_model: "google-main/google-main-model"
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
                    default_model: "openai-main/openai-default"
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


def test_repo_detects_provider_config_updates_without_manual_reload(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: "openai-main/openai-default"
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

    from agent.shared.config import get_config_service

    get_config_service().update_setting(
        "llm.providers.openai-main.models",
        ["openai-default", "openai-fast"],
    )

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
                    default_model: "openai-main/global-model"
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
                    default_model: "google-main/google-model"
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
                    default_model: "google-main/google-provider-model"
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
                    default_model: "google-main/google-model"
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
    with pytest.raises(ValueError, match="llm.default_model"):
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
            default_model: "openai-main/provider-model"
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


# --- Application: resolve_chat_model error handling ---


def test_resolve_chat_model_raises_when_provider_missing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: "openai-main/openai-model"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "openai-key"
                    base_url: "https://openai.example/v1"
                    default_model: "openai-model"
                google-main:
                    type: "google"
                    api_key: "google-key"
                    default_model: "google-model"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    openai_factory = MagicMock()
    google_factory = MagicMock()

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, openai_factory)
    service.register_factory(ProviderType.GOOGLE, google_factory)

    with pytest.raises(KeyError, match="Provider not found"):
        resolve_chat_model(service, provider_name="missing-provider")

    openai_factory.create.assert_not_called()
    google_factory.create.assert_not_called()

    _get_cached_model.cache_clear()


def test_resolve_chat_model_raises_when_model_missing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: ""
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "openai-key"
                    base_url: "https://openai.example/v1"
                    default_model: ""
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    openai_factory = MagicMock()
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, openai_factory)

    with pytest.raises(RuntimeError, match="Model not configured"):
        resolve_chat_model(service)

    openai_factory.create.assert_not_called()

    _get_cached_model.cache_clear()


def test_resolve_chat_model_resolves_primary_when_configured(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: "openai-main/openai-model"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "openai-key"
                    base_url: "https://openai.example/v1"
                    default_model: "openai-model"
                google-main:
                    type: "google"
                    api_key: "google-key"
                    default_model: "google-model"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    openai_model = MagicMock(name="openai_model")
    openai_factory = MagicMock()
    openai_factory.create.return_value = openai_model

    google_factory = MagicMock()

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, openai_factory)
    service.register_factory(ProviderType.GOOGLE, google_factory)

    result = resolve_chat_model(service)

    assert result is openai_model
    openai_factory.create.assert_called_once()
    google_factory.create.assert_not_called()

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


# --- Provider catalog runtime file ---


def test_provider_catalog_path_uses_home_data_dir(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(catalog_module.Path, "home", staticmethod(lambda: tmp_path))

    assert Path(catalog_module.get_api_json_path()) == (
        tmp_path / ".k41-agent" / "data" / "api.json"
    )


@pytest.mark.asyncio
async def test_ensure_catalog_available_seeds_from_legacy_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(catalog_module.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(catalog_module, "_catalog_cache", None)

    legacy_path = tmp_path / "legacy-api.json"
    legacy_path.write_text(
        '{"openai":{"name":"OpenAI","models":{"gpt-legacy":{"name":"GPT Legacy"}}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_module, "_legacy_api_json_path", lambda: legacy_path)

    success, message = await catalog_module.ensure_catalog_available()

    catalog_path = tmp_path / ".k41-agent" / "data" / "api.json"
    loaded_catalog = catalog_module.load_providers_catalog(force_reload=True)
    assert success is True
    assert "Provider catalog seeded" in message
    assert catalog_path.exists()
    assert loaded_catalog["openai"].models[0].id == "gpt-legacy"


@pytest.mark.asyncio
async def test_ensure_catalog_available_downloads_missing_runtime_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    import httpx

    monkeypatch.setattr(catalog_module.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        catalog_module,
        "_legacy_api_json_path",
        lambda: tmp_path / "missing-api.json",
    )
    monkeypatch.setattr(catalog_module, "_catalog_cache", None)

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {
                "openai": {
                    "name": "OpenAI",
                    "npm": "",
                    "api": "https://api.openai.com/v1",
                    "models": {
                        "gpt-test": {
                            "name": "GPT Test",
                            "modalities": {"input": ["text"], "output": ["text"]},
                        }
                    },
                }
            }

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str) -> FakeResponse:
            assert url == catalog_module.MODELS_DEV_API_URL
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    success, message = await catalog_module.ensure_catalog_available()

    catalog_path = tmp_path / ".k41-agent" / "data" / "api.json"
    assert success is True
    assert "Catalog updated successfully" in message
    assert catalog_path.exists()
    loaded_catalog = catalog_module.load_providers_catalog(force_reload=True)
    assert loaded_catalog["openai"].models[0].id == "gpt-test"


@pytest.mark.asyncio
async def test_update_catalog_skips_write_when_content_is_unchanged(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    import httpx

    monkeypatch.setattr(catalog_module.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        catalog_module,
        "_legacy_api_json_path",
        lambda: tmp_path / "missing-api.json",
    )
    monkeypatch.setattr(catalog_module, "_catalog_cache", None)

    catalog_path = tmp_path / ".k41-agent" / "data" / "api.json"
    catalog_path.parent.mkdir(parents=True)
    content = {"openai": {"name": "OpenAI", "models": {"gpt-test": {"name": "GPT Test"}}}}
    catalog_path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    original_stat = catalog_path.stat()

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return content

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str) -> FakeResponse:
            assert url == catalog_module.MODELS_DEV_API_URL
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    success, message = await catalog_module.update_catalog_from_url()

    assert success is True
    assert "already up to date" in message
    assert catalog_path.stat().st_mtime_ns == original_stat.st_mtime_ns


# --- Public API import ---


def test_get_chat_model_import() -> None:
    from agent.modules.providers import get_chat_model  # noqa: F401

    assert callable(get_chat_model)


# --- extra_body config ---


def test_repo_model_profiles_json_and_runtime_updates(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _set_config_path(monkeypatch, config_path)
    from agent.shared.config import get_config_service

    config = get_config_service()
    key = "llm.providers.openai-main.model_profiles"
    profiles = {"test-model": {"reasoning_effort_levels": ["low", "high"], "reasoning_effort_default": "high"}}
    config.update_setting(key, json.dumps(profiles))
    repo = ConfigProviderRepository()
    assert repo.get_provider("openai-main").model_profiles == profiles
    config.update_setting(key, {"test-model": {"reasoning_effort_levels": ["low"]}})
    assert repo.get_provider("openai-main").model_profiles["test-model"]["reasoning_effort_levels"] == ["low"]
    config.update_setting(key, "")
    assert repo.get_provider("openai-main").model_profiles == {}


def test_provider_config_extra_body_default() -> None:
    config = ProviderConfig(
        name="test",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.test.com/v1",
        api_key="test-key",
        default_model="test-model",
    )
    assert config.extra_body is None


def test_provider_config_with_extra_body() -> None:
    config = ProviderConfig(
        name="test",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.test.com/v1",
        api_key="test-key",
        default_model="test-model",
        extra_body={"thinking": {"type": "enabled"}},
    )
    assert config.extra_body == {"thinking": {"type": "enabled"}}


def test_repo_extra_body_from_json_string(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: "openai-main/test-model"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "test-key"
                    default_model: "test-model"
                    extra_body: '{"thinking": {"type": "enabled"}}'
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_provider("openai-main")

    assert provider.extra_body == {"thinking": {"type": "enabled"}}


def test_repo_extra_body_empty_string(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: "openai-main/test-model"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "test-key"
                    default_model: "test-model"
                    extra_body: ""
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_provider("openai-main")

    assert provider.extra_body is None


def test_repo_extra_body_invalid_json(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: "openai-main/test-model"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "test-key"
                    default_model: "test-model"
                    extra_body: "not valid json"
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_provider("openai-main")

    assert provider.extra_body is None


def test_repo_extra_body_not_set(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, api_key="test-key", default_model="test-model")
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    provider = repo.get_provider("openai-main")

    assert provider.extra_body is None


def test_resolve_chat_model_passes_extra_body_to_factory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _get_cached_model.cache_clear()

    config_path = tmp_path / "config.yaml"
    _write_yaml(
        config_path,
        """
        llm:
            default_model: "openai-main/test-model"
            providers:
                openai-main:
                    type: "openai_compatible"
                    api_key: "test-key"
                    default_model: "test-model"
                    extra_body: '{"custom_param": true}'
        """,
    )
    _set_config_path(monkeypatch, config_path)

    repo = ConfigProviderRepository()
    service = ProviderService(repository=repo)

    mock_model = MagicMock()
    mock_factory = MagicMock()
    mock_factory.create.return_value = mock_model

    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    resolve_chat_model(service)

    mock_factory.create.assert_called_once()
    provider_config_arg = mock_factory.create.call_args.args[0]
    assert provider_config_arg.extra_body == {"custom_param": True}

    _get_cached_model.cache_clear()


def test_factory_enables_stream_usage_for_token_tracking() -> None:
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

    factory = OpenAICompatibleFactory()
    provider = ProviderConfig(
        name="cl-nvidia",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://gateway.ai.cloudflare.com/v1/account/default/custom-nvidia/v1",
        api_key="test",
        default_model="nvidia/nemotron-3.5-lightning-30b-a3b",
    )
    model = ModelConfig(model_name="nvidia/nemotron-3.5-lightning-30b-a3b")
    result = factory.create(provider, model, "test-key")

    assert result.stream_usage is True


def test_factory_nvidia_minimax_auto_injects_thinking() -> None:
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

    factory = OpenAICompatibleFactory()
    provider = ProviderConfig(
        name="nvidia",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test",
        default_model="minimaxai/minimax-m3",
    )
    model = ModelConfig(model_name="minimaxai/minimax-m3")
    result = factory.create(provider, model, "test-key")

    assert result.extra_body == {"thinking": {"type": "enabled"}}


def test_factory_nvidia_minimax_user_override() -> None:
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

    factory = OpenAICompatibleFactory()
    provider = ProviderConfig(
        name="nvidia",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test",
        default_model="minimaxai/minimax-m3",
        extra_body={"thinking": {"type": "disabled"}},
    )
    model = ModelConfig(model_name="minimaxai/minimax-m3")
    result = factory.create(provider, model, "test-key")

    assert result.extra_body == {"thinking": {"type": "disabled"}}


def test_factory_nvidia_non_minimax_no_injection() -> None:
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

    factory = OpenAICompatibleFactory()
    provider = ProviderConfig(
        name="nvidia",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test",
        default_model="meta/llama-3.1-405b-instruct",
    )
    model = ModelConfig(model_name="meta/llama-3.1-405b-instruct")
    result = factory.create(provider, model, "test-key")

    assert result.extra_body is None


def test_factory_non_nvidia_minimax_no_injection() -> None:
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

    factory = OpenAICompatibleFactory()
    provider = ProviderConfig(
        name="custom",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.example.com/v1",
        api_key="test",
        default_model="minimax-m3",
    )
    model = ModelConfig(model_name="minimax-m3")
    result = factory.create(provider, model, "test-key")

    assert result.extra_body is None


def test_factory_merges_fallback_with_user_extra_body() -> None:
    from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

    factory = OpenAICompatibleFactory()
    provider = ProviderConfig(
        name="nvidia",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test",
        default_model="minimaxai/minimax-m3",
        extra_body={"reasoning_split": True},
    )
    model = ModelConfig(model_name="minimaxai/minimax-m3")
    result = factory.create(provider, model, "test-key")

    assert result.extra_body == {
        "thinking": {"type": "enabled"},
        "reasoning_split": True,
    }


# --- Provider Connection Verification & Model Discovery Tests ---

def test_resolve_suggested_default_model_empty_and_fallback() -> None:
    from agent.modules.providers import resolve_suggested_default_model

    assert resolve_suggested_default_model([]) == ""
    assert resolve_suggested_default_model(["custom-model-alpha", "custom-model-beta"]) == "custom-model-alpha"


def test_resolve_suggested_default_model_catalog_priority() -> None:
    from agent.modules.providers import resolve_suggested_default_model

    models = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-1.5-pro"]
    assert resolve_suggested_default_model(models, catalog_default="gemini-2.5-pro") == "gemini-2.5-pro"
    assert resolve_suggested_default_model(models, catalog_default="google/gemini-2.5-flash") == "gemini-2.5-flash"


def test_resolve_suggested_default_model_keyword_priority() -> None:
    from agent.modules.providers import resolve_suggested_default_model

    # flash has highest keyword priority
    assert resolve_suggested_default_model(["gpt-4o-mini", "gemini-2.5-flash", "claude-3-5-sonnet"]) == "gemini-2.5-flash"
    # mini has priority over sonnet
    assert resolve_suggested_default_model(["claude-3-5-sonnet", "gpt-4o-mini"]) == "gpt-4o-mini"
    # sonnet has priority over generic
    assert resolve_suggested_default_model(["deepseek-chat", "claude-3-5-sonnet"]) == "claude-3-5-sonnet"


@pytest.mark.asyncio
async def test_verify_provider_connection_openai_compatible_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.modules.providers import verify_provider_connection
    import httpx

    fake_response = httpx.Response(
        200,
        json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
        request=httpx.Request("GET", "https://api.openai.com/v1/models"),
    )

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers=None):
            return fake_response

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    result = await verify_provider_connection(
        provider_type="openai_compatible",
        api_key="sk-valid-key",
        base_url="https://api.openai.com/v1",
    )

    assert result.ok is True
    assert result.error_code is None
    assert "gpt-4o-mini" in result.models
    assert "gpt-4o" in result.models
    assert result.suggested_default_model == "gpt-4o-mini"
    assert result.latency_ms is not None and result.latency_ms >= 0


@pytest.mark.asyncio
async def test_verify_provider_connection_invalid_config() -> None:
    from agent.modules.providers import verify_provider_connection

    # Missing API key
    res1 = await verify_provider_connection("google", "")
    assert res1.ok is False
    assert res1.error_code == "INVALID_CONFIG"

    # Missing Base URL for OpenAI-compatible
    res2 = await verify_provider_connection("openai_compatible", "key", base_url="")
    assert res2.ok is False
    assert res2.error_code == "INVALID_CONFIG"

    # Unknown provider type
    res3 = await verify_provider_connection("unknown_provider", "key")
    assert res3.ok is False
    assert res3.error_code == "INVALID_CONFIG"


@pytest.mark.asyncio
async def test_verify_provider_connection_auth_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.modules.providers import verify_provider_connection
    import httpx

    fake_response = httpx.Response(
        401,
        text="Invalid API key",
        request=httpx.Request("GET", "https://api.openai.com/v1/models"),
    )

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers=None):
            raise httpx.HTTPStatusError("Unauthorized", request=fake_response.request, response=fake_response)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    result = await verify_provider_connection(
        provider_type="openai_compatible",
        api_key="sk-bad-key",
        base_url="https://api.openai.com/v1",
    )

    assert result.ok is False
    assert result.error_code == "AUTH_FAILED"
    assert "Authentication failed" in result.message
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_verify_provider_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.modules.providers import verify_provider_connection
    import httpx

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers=None):
            raise httpx.ReadTimeout("Timed out waiting for response")

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    result = await verify_provider_connection(
        provider_type="openai_compatible",
        api_key="sk-test-key",
        base_url="https://api.openai.com/v1",
    )

    assert result.ok is False
    assert result.error_code == "TIMEOUT"
    assert "timed out" in result.message.lower()


@pytest.mark.asyncio
async def test_verify_provider_connection_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.modules.providers import verify_provider_connection
    import httpx

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers=None):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    result = await verify_provider_connection(
        provider_type="openai_compatible",
        api_key="sk-test-key",
        base_url="https://api.openai.com/v1",
    )

    assert result.ok is False
    assert result.error_code == "CONNECTION_ERROR"
    assert "connect" in result.message.lower()


@pytest.mark.asyncio
async def test_verify_provider_connection_with_mock_factory() -> None:
    from agent.modules.providers import verify_provider_connection
    from unittest.mock import AsyncMock

    mock_factory = MagicMock()
    mock_factory.list_models = AsyncMock(return_value=["model-a", "model-flash-2"])

    result = await verify_provider_connection(
        provider_type="google",
        api_key="dummy-key",
        factory=mock_factory,
    )

    assert result.ok is True
    assert result.models == ["model-a", "model-flash-2"]
    assert result.suggested_default_model == "model-flash-2"


@pytest.mark.asyncio
async def test_provider_service_verify_provider_method() -> None:
    from agent.modules.providers.service import ProviderService
    from unittest.mock import AsyncMock

    mock_repo = MagicMock()
    service = ProviderService(mock_repo)

    mock_factory = MagicMock()
    mock_factory.list_models = AsyncMock(return_value=["test-model", "test-mini"])
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, mock_factory)

    result = await service.verify_provider(
        provider_type="openai_compatible",
        api_key="some-key",
        base_url="https://api.example.com/v1",
    )

    assert result.ok is True
    assert result.suggested_default_model == "test-mini"

