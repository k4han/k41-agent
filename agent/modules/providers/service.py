"""Provider service — manages provider configurations and factory registry."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from agent.modules.providers.models import ModelOption, ProviderModelCatalog
from agent.modules.providers.ports import ChatModelFactory, ProviderRepository
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.providers.profiles import get_model_profile, reasoning_metadata

if TYPE_CHECKING:
    from agent.modules.providers.verification import ProviderVerificationResult


class ProviderService:
    """Central service for managing provider configs and their factories."""

    def __init__(self, repository: ProviderRepository) -> None:
        self._repository = repository
        self._factories: dict[ProviderType, ChatModelFactory] = {}
        self._named_factories: dict[str, ChatModelFactory] = {}

    # --- Factory registration ---

    def register_factory(
        self, provider_type: ProviderType, factory: ChatModelFactory
    ) -> None:
        self._factories[provider_type] = factory

    def register_provider_factory(
        self, provider_name: str, factory: ChatModelFactory
    ) -> None:
        """Register a factory for a specific provider name."""
        from agent.modules.providers.catalog import normalize_provider_key

        self._named_factories[normalize_provider_key(provider_name)] = factory

    def get_factory(
        self,
        provider_type: ProviderType,
        provider_name: str | None = None,
        catalog_id: str = "",
    ) -> ChatModelFactory:
        from agent.modules.providers.catalog import normalize_provider_key

        def _norm_type(value: object) -> str:
            raw = str(value.value if isinstance(value, ProviderType) else value)
            norm = raw.strip().lower().replace("-", "_")
            if norm == "openai":
                norm = "openai_compatible"
            return norm

        requested = _norm_type(provider_type)
        # Only reuse a named factory when it matches the requested type.
        # Provider names are user-chosen and may collide across types,
        # so a blind name lookup can return a factory for the wrong driver.
        if provider_name:
            named = self._named_factories.get(normalize_provider_key(provider_name))
            if named is not None:
                try:
                    stored = self._repository.get_provider(provider_name)
                    if _norm_type(stored.provider_type) == requested:
                        return named
                except Exception:
                    # Provider not in repository (new candidate) -> do not reuse.
                    pass
        if catalog_id:
            named = self._named_factories.get(normalize_provider_key(catalog_id))
            if named is not None:
                try:
                    from agent.modules.providers.catalog import get_provider_catalog_entry

                    entry = get_provider_catalog_entry(catalog_id)
                    if entry is not None and _norm_type(entry.provider_type) == requested:
                        return named
                except Exception:
                    pass
        lookup_type = provider_type
        if requested == "openai_compatible" and provider_type == ProviderType.OPENAI:
            lookup_type = ProviderType.OPENAI_COMPATIBLE
        factory = self._factories.get(lookup_type)
        if factory is None and lookup_type != provider_type:
            factory = self._factories.get(provider_type)
        if factory is None:
            raise RuntimeError(
                f"No factory registered for provider type: {provider_type}"
            )
        return factory

    # --- Provider queries ---

    def get_provider(self, name: str) -> ProviderConfig:
        return self._repository.get_provider(name)

    def get_default_provider(self) -> ProviderConfig:
        return self._repository.get_default_provider()

    def list_providers(self) -> list[ProviderConfig]:
        return self._repository.list_providers()

    async def list_model_catalog(
        self,
        provider_name: str | None = None,
        *,
        include_remote: bool = False,
    ) -> ProviderModelCatalog:
        provider = (
            self.get_provider(provider_name)
            if provider_name
            else self.get_default_provider()
        )
        factory = self.get_factory(provider.provider_type, provider_name=provider.name, catalog_id=provider.catalog_id)
        list_models = getattr(factory, "list_models", None)
        can_list_models = callable(list_models)

        remote_models: list[str] = []
        error: str | None = None
        if include_remote and can_list_models:
            try:
                result = list_models(provider, provider.api_key)
                if inspect.isawaitable(result):
                    result = await result
                remote_models = [
                    str(model).strip()
                    for model in result
                    if str(model).strip()
                ]
            except Exception as exc:
                error = str(exc)

        return ProviderModelCatalog(
            provider=provider.name,
            provider_type=str(provider.provider_type),
            default_model=provider.default_model,
            can_list_models=can_list_models,
            models=_merge_model_options(
                provider_name=provider.catalog_id or provider.name,
                remote_models=remote_models,
                configured_models=list(provider.models),
                default_model=provider.default_model,
                provider_type=str(provider.provider_type),
                model_profiles=provider.model_profiles,
            ),
            error=error,
        )

    async def list_model_catalogs(
        self,
        *,
        include_remote: bool = False,
    ) -> list[ProviderModelCatalog]:
        catalogs = []
        for provider in self.list_providers():
            catalogs.append(
                await self.list_model_catalog(
                    provider.name,
                    include_remote=include_remote,
                )
            )
        return catalogs

    def reload(self) -> None:
        self._repository.reload()

    async def verify_provider(
        self,
        provider_type: ProviderType | str,
        api_key: str,
        base_url: str = "",
        *,
        catalog_id: str = "",
        provider_name: str = "",
        timeout: float = 10.0,
    ) -> ProviderVerificationResult:
        from agent.modules.providers.verification import (
            ProviderVerificationResult,
            verify_provider_connection,
        )

        norm_type = str(provider_type).strip().lower().replace("-", "_")
        if norm_type == "openai":
            norm_type = "openai_compatible"
            if not str(base_url or "").strip():
                base_url = "https://api.openai.com/v1"
        try:
            provider_enum = ProviderType(norm_type)
        except ValueError:
            return ProviderVerificationResult(
                ok=False,
                message=f"Unsupported provider type: {provider_type}.",
                error_code="INVALID_CONFIG",
                latency_ms=0,
            )
        try:
            factory: ChatModelFactory | None = self.get_factory(
                provider_enum,
                provider_name=provider_name,
                catalog_id=catalog_id,
            )
        except Exception:
            factory = None
        return await verify_provider_connection(
            provider_type=norm_type,
            api_key=api_key,
            base_url=base_url,
            catalog_id=catalog_id,
            provider_name=provider_name,
            factory=factory,
            timeout=timeout,
        )


def _merge_model_options(
    *,
    provider_name: str,
    remote_models: list[str],
    configured_models: list[str],
    default_model: str,
    provider_type: str = "openai_compatible",
    model_profiles: dict | None = None,
) -> tuple[ModelOption, ...]:
    from agent.modules.providers.catalog import get_provider_catalog_entry

    catalog_entry = get_provider_catalog_entry(provider_name)
    model_entries = {m.id: m for m in catalog_entry.models} if catalog_entry else {}

    options: dict[str, ModelOption] = {}

    def add(model_id: str, source: str) -> None:
        normalized = model_id.strip()
        if normalized and normalized not in options:
            entry = model_entries.get(normalized)
            from agent.modules.providers.context_window import resolve_context_window

            profile = get_model_profile(provider_type, normalized, model_profiles)
            context_window = resolve_context_window(provider_name, normalized, profile)
            input_types = entry.input_types if entry else None
            output_types = entry.output_types if entry else None
            levels, default = reasoning_metadata(profile)
            options[normalized] = ModelOption(
                id=normalized,
                label=normalized,
                source=source,
                context_window=context_window,
                input_types=input_types,
                output_types=output_types,
                reasoning_effort_levels=levels,
                reasoning_effort_default=default,
            )

    for model_id in remote_models:
        add(model_id, "live")
    for model_id in configured_models:
        add(model_id, "config")
    for model_id in model_profiles or {}:
        add(model_id, "config")
    add(default_model, "default")

    return tuple(options.values())
