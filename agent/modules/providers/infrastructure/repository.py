"""Provider repository with config service support.

Reads provider configuration from the centralized config service.
"""

from __future__ import annotations

from typing import Any

from agent.modules.providers.domain.provider import ProviderConfig, ProviderType
from agent.shared.config import get_config_service, parse_provider_key
from agent.shared.infrastructure.config_file import coerce_bool
from agent.shared.infrastructure.validation import is_placeholder_value


DEFAULT_MODEL = ""
DEFAULT_BASE_URL = ""
DEFAULT_PROVIDER = "openai_compatible"
DEFAULT_PROVIDER_NAME = "default"

_PROVIDER_ALIASES: dict[str, ProviderType] = {
    "openai_compatible": ProviderType.OPENAI_COMPATIBLE,
    "google": ProviderType.GOOGLE,
}


def _normalize_provider_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _resolve_provider_type_for_key(value: str, key_name: str) -> ProviderType:
    normalized = _normalize_provider_name(value)
    provider_type = _PROVIDER_ALIASES.get(normalized)
    if provider_type is None:
        supported = ", ".join(sorted(_PROVIDER_ALIASES))
        raise ValueError(
            f"Unsupported {key_name} value: {value!r}. Supported values: {supported}."
        )
    return provider_type


def _extract_provider_entries(flat_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for key, value in flat_config.items():
        parsed = parse_provider_key(key)
        if parsed is None:
            continue

        provider_name_raw, field_name = parsed
        provider_key = _normalize_provider_name(provider_name_raw)
        if not provider_key:
            continue

        provider_name = provider_name_raw.strip()
        entry = providers.setdefault(
            provider_key,
            {"_provider_name": provider_name or provider_key},
        )
        entry[field_name] = value

    return providers


def _resolve_default_model(
    provider_values: dict[str, Any],
    global_default_model: str,
) -> str:
    value = provider_values.get("default_model")
    if isinstance(value, str) and value.strip():
        return value.strip()

    if global_default_model:
        return global_default_model

    return DEFAULT_MODEL


def _resolve_api_key(
    provider_name: str,
    provider_values: dict[str, Any],
    shared_api_key: str,
    *,
    enabled: bool,
) -> str:
    raw_api_key = provider_values.get("api_key", shared_api_key)
    api_key = str(raw_api_key).strip() if raw_api_key is not None else ""

    if not enabled:
        return api_key

    if is_placeholder_value(api_key):
        raise RuntimeError(
            "LLM API key not configured. "
            f"Please set 'llm.providers.{provider_name}.api_key' "
            "(or fallback 'llm.api_key') in ~/.kaka-agent/config.yaml"
        )

    return api_key


def _build_provider_config(
    provider_key: str,
    provider_values: dict[str, Any],
    *,
    shared_api_key: str,
    shared_base_url: str,
    global_default_model: str,
) -> ProviderConfig:
    provider_name = str(provider_values.get("_provider_name", provider_key)).strip() or provider_key
    provider_type_value = (
        provider_values.get("provider")
        or provider_values.get("type")
        or provider_key
    )
    provider_type = _resolve_provider_type_for_key(
        str(provider_type_value),
        f"llm.providers.{provider_name}.provider",
    )
    enabled = coerce_bool(provider_values.get("enabled", True))

    base_url = str(provider_values.get("base_url", shared_base_url)).strip()
    if provider_type == ProviderType.OPENAI_COMPATIBLE:
        base_url = base_url or DEFAULT_BASE_URL
    else:
        base_url = ""

    return ProviderConfig(
        name=provider_name,
        provider_type=provider_type,
        base_url=base_url,
        api_key=_resolve_api_key(
            provider_name,
            provider_values,
            shared_api_key,
            enabled=enabled,
        ),
        default_model=_resolve_default_model(
            provider_values,
            global_default_model,
        ),
        enabled=enabled,
    )


class ConfigProviderRepository:
    """Resolve provider configs from config service."""

    def __init__(self) -> None:
        self._cache: tuple[dict[str, ProviderConfig], str] | None = None

    def reload(self) -> None:
        self._cache = None

    def _resolve_default_provider_name(
        self,
        providers: dict[str, ProviderConfig],
    ) -> str:
        config = get_config_service()
        configured_default = config.get_str("llm.default_provider", "").strip()

        if not configured_default:
            if DEFAULT_PROVIDER_NAME in providers:
                return DEFAULT_PROVIDER_NAME

            for name, provider in providers.items():
                if provider.enabled:
                    return name

            raise RuntimeError(
                "No enabled providers configured. "
                "Please configure at least one provider in llm.providers."
            )

        normalized = _normalize_provider_name(configured_default)
        if normalized in providers:
            return normalized

        provider_type = _PROVIDER_ALIASES.get(normalized)
        if provider_type is not None:
            matched = [
                name
                for name, provider in providers.items()
                if provider.provider_type == provider_type
            ]
            if len(matched) == 1:
                return matched[0]
            if len(matched) > 1:
                names = ", ".join(sorted(matched))
                raise ValueError(
                    "Ambiguous llm.default_provider value: "
                    f"{configured_default!r} matches multiple configured providers: {names}."
                )

        available = ", ".join(sorted(providers))
        raise ValueError(
            f"Unknown llm.default_provider value: {configured_default!r}. "
            f"Available providers: {available}."
        )

    def _load(self) -> tuple[dict[str, ProviderConfig], str]:
        if self._cache is not None:
            return self._cache
        config = get_config_service()

        shared_api_key = config.get_str("llm.api_key", "").strip()
        shared_base_url = config.get_str("llm.base_url", DEFAULT_BASE_URL).strip()
        global_default_model = config.get_str("llm.default_model", "").strip()

        providers = _extract_provider_entries(config.get_all())
        if providers:
            loaded: dict[str, ProviderConfig] = {}
            for provider_name, provider_values in providers.items():
                loaded[provider_name] = _build_provider_config(
                    provider_name,
                    provider_values,
                    shared_api_key=shared_api_key,
                    shared_base_url=shared_base_url,
                    global_default_model=global_default_model,
                )

            default_provider_name = self._resolve_default_provider_name(loaded)
            if not loaded[default_provider_name].enabled:
                raise RuntimeError(
                    f"Default provider {default_provider_name!r} is disabled. "
                    "Set llm.default_provider to an enabled provider."
                )
            self._cache = (loaded, default_provider_name)
            return self._cache

        configured_default_provider = config.get_str("llm.default_provider", "").strip()
        resolved_default_provider = configured_default_provider or DEFAULT_PROVIDER
        provider_type = _resolve_provider_type_for_key(
            resolved_default_provider,
            "llm.default_provider",
        )

        default_provider = ProviderConfig(
            name=DEFAULT_PROVIDER_NAME,
            provider_type=provider_type,
            base_url=(shared_base_url or DEFAULT_BASE_URL)
            if provider_type == ProviderType.OPENAI_COMPATIBLE
            else "",
            api_key=_resolve_api_key(
                DEFAULT_PROVIDER_NAME,
                {},
                shared_api_key,
                enabled=True,
            ),
            default_model=global_default_model
            or DEFAULT_MODEL,
            enabled=True,
        )

        self._cache = ({DEFAULT_PROVIDER_NAME: default_provider}, DEFAULT_PROVIDER_NAME)
        return self._cache

    def get_provider(self, name: str) -> ProviderConfig:
        providers, default_provider_name = self._load()
        default_provider = providers[default_provider_name]

        normalized_name = _normalize_provider_name(name)
        if normalized_name == "default":
            return default_provider

        direct_match = providers.get(normalized_name)
        if direct_match is not None:
            return direct_match

        requested_provider_type = _PROVIDER_ALIASES.get(normalized_name)
        if requested_provider_type is not None:
            matched = [
                provider
                for provider in providers.values()
                if provider.provider_type == requested_provider_type
            ]
            if len(matched) == 1:
                return matched[0]

        raise KeyError(f"Provider not found: {name!r}")

    def get_default_provider(self) -> ProviderConfig:
        return self.get_provider("default")

    def list_providers(self) -> list[ProviderConfig]:
        providers, _ = self._load()
        return list(providers.values())
