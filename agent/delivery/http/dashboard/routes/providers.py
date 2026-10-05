from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from agent.delivery.http.dashboard.routes.helpers.agents import (
    invalidate_agent_provider_options_cache,
)
from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.urls import base_url_error
from agent.delivery.http.dashboard.routes.helpers.providers import (
    normalize_provider_name,
    provider_config_name,
    provider_entries_from_flat_config,
    provider_type_from_body,
    serialize_model_catalog,
    validate_provider_name,
)
from agent.delivery.http.dashboard.routes.helpers.settings import (
    backend_settings_payload,
    delete_config_tree,
    settings_payload,
    update_config_settings,
)
from agent.modules.providers import (
    list_provider_model_catalog,
    list_provider_model_catalogs,
    verify_provider_connection,
)


router = APIRouter()


@router.get("/dashboard-api/config")
async def get_dashboard_config(request: Request) -> dict[str, Any]:
    return await settings_payload(request, include_provider_settings=False)


@router.get("/dashboard-api/backends")
async def get_dashboard_backends(request: Request) -> dict[str, Any]:
    return await backend_settings_payload(request)


@router.get("/dashboard-api/providers")
async def get_dashboard_providers(request: Request) -> dict[str, Any]:
    return await settings_payload(request, include_provider_settings=True)


class VerifyProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    type: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    catalog_id: str | None = None


class ProviderVerificationResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
    models: list[str] = Field(default_factory=list)
    suggested_default_model: str = ""
    error_code: str | None = None
    details: dict[str, Any] | None = None


@router.post("/dashboard-api/providers/verify")
async def verify_dashboard_provider(
    body: VerifyProviderBody,
    request: Request,
) -> ProviderVerificationResponse:
    service = get_request_config_service(request)
    provider_name = body.name.strip() if body.name else ""
    explicit_type = bool(body.type and body.type.strip())
    provider_type = body.type.strip() if body.type else ""
    # None means field omitted -> fall back to saved value.
    # Empty string means explicitly cleared -> do not fall back.
    api_key = body.api_key.strip() if body.api_key is not None else None
    base_url = body.base_url.strip() if body.base_url is not None else None
    catalog_id = body.catalog_id.strip() if body.catalog_id is not None else None
    # Only pass provider_name for factory lookup when verifying the same type.
    # An explicit different type is a fresh candidate and must not reuse a
    # named factory registered for the saved type.
    same_type_for_verify = True

    if provider_name:
        existing_name = provider_config_name(service, provider_name)
        if existing_name is not None:
            saved_type = str(service.get(f"llm.providers.{existing_name}.type", "") or "").strip()
            if not provider_type:
                provider_type = saved_type
            norm_candidate = provider_type.lower().replace("-", "_")
            if norm_candidate == "openai":
                norm_candidate = "openai_compatible"
            norm_saved = saved_type.lower().replace("-", "_")
            if norm_saved == "openai":
                norm_saved = "openai_compatible"
            # Only reuse saved credentials when testing the same provider type.
            # An explicit different type means a fresh candidate, not an override.
            if (not explicit_type) or (norm_candidate == norm_saved):
                if api_key is None:
                    api_key = str(service.get(f"llm.providers.{existing_name}.api_key", "") or "").strip()
                if base_url is None:
                    base_url = str(service.get(f"llm.providers.{existing_name}.base_url", "") or "").strip()
                if catalog_id is None:
                    catalog_id = str(service.get(f"llm.providers.{existing_name}.catalog_id", "") or "").strip()
                same_type_for_verify = True
            else:
                same_type_for_verify = False
        elif not provider_type:
            return ProviderVerificationResponse(
                ok=False,
                message=f"Provider '{provider_name}' not found.",
                error_code="INVALID_CONFIG",
                latency_ms=0,
            )

    api_key = api_key or ""
    base_url = base_url or ""
    catalog_id = catalog_id or ""

    if not provider_type:
        return ProviderVerificationResponse(
            ok=False,
            message="Provider type is required.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    norm_type = provider_type.lower().replace("-", "_")
    if norm_type == "openai":
        norm_type = "openai_compatible"
        if not base_url:
            base_url = "https://api.openai.com/v1"

    if norm_type not in ("google", "anthropic", "openai_compatible"):
        return ProviderVerificationResponse(
            ok=False,
            message=f"Unsupported provider type: {provider_type}.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    if not api_key:
        return ProviderVerificationResponse(
            ok=False,
            message="API key is required.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    if norm_type == "openai_compatible" and not base_url:
        return ProviderVerificationResponse(
            ok=False,
            message="Base URL is required for OpenAI-compatible providers.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    if base_url:
        url_err = base_url_error(base_url)
        if url_err:
            return ProviderVerificationResponse(
                ok=False,
                message=f"Invalid base URL: {url_err}",
                error_code="INVALID_CONFIG",
                latency_ms=0,
            )

    result = await verify_provider_connection(
        provider_type=norm_type,
        api_key=api_key,
        base_url=base_url,
        catalog_id=catalog_id,
        provider_name=provider_name if same_type_for_verify else "",
    )
    return ProviderVerificationResponse(
        ok=result.ok,
        message=result.message,
        latency_ms=result.latency_ms,
        models=result.models,
        suggested_default_model=result.suggested_default_model,
        error_code=result.error_code,
        details=result.details,
    )


class CreateProviderBody(BaseModel):
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    base_url: str = ""
    catalog_id: str | None = None
    default_model: str = ""
    models: list[str] = Field(default_factory=list)


@router.post("/dashboard-api/providers")
async def create_dashboard_provider(
    body: CreateProviderBody,
    request: Request,
) -> dict[str, str]:
    service = get_request_config_service(request)
    provider_name = validate_provider_name(body.name)
    provider_type = provider_type_from_body(body.type)

    providers = provider_entries_from_flat_config(service.get_all())
    if normalize_provider_name(provider_name) in providers:
        raise HTTPException(
            status_code=409,
            detail=f"Provider already exists: {provider_name}.",
        )

    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required.")

    base_url = body.base_url.strip()
    if provider_type == "openai_compatible" and not base_url:
        raise HTTPException(
            status_code=400,
            detail="Base URL is required for OpenAI-compatible providers.",
        )
    if base_url:
        url_err = base_url_error(base_url)
        if url_err:
            raise HTTPException(status_code=400, detail=f"Invalid base URL: {url_err}")

    if body.catalog_id:
        from agent.modules.providers import load_providers_catalog
        catalog = load_providers_catalog()
        entry = next((entry for entry in catalog.values() if entry.id == body.catalog_id), None)
        if entry is None or entry.provider_type != provider_type:
            raise HTTPException(400, "Invalid provider catalog entry.")

    models_list = [m.strip() for m in body.models if m and m.strip()]
    default_model = body.default_model.strip()
    if default_model and default_model not in models_list:
        models_list.append(default_model)

    values: dict[str, Any | None] = {
        f"llm.providers.{provider_name}.type": provider_type,
        f"llm.providers.{provider_name}.api_key": api_key,
        f"llm.providers.{provider_name}.default_model": default_model,
        f"llm.providers.{provider_name}.models": models_list,
        f"llm.providers.{provider_name}.enabled": True,
    }
    if body.catalog_id:
        values[f"llm.providers.{provider_name}.catalog_id"] = body.catalog_id
    if provider_type == "openai_compatible":
        values[f"llm.providers.{provider_name}.base_url"] = base_url

    update_config_settings(service, values, require_writable=True)
    invalidate_agent_provider_options_cache()
    return {"status": "created", "name": provider_name, "type": provider_type}


@router.delete("/dashboard-api/providers/{provider_name}")
async def delete_dashboard_provider(
    provider_name: str,
    request: Request,
) -> dict[str, str]:
    service = get_request_config_service(request)
    existing_name = provider_config_name(service, provider_name)
    if existing_name is None:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_name}.")

    default_model_val = str(service.get("llm.default_model", "") or "").strip()
    configured_default = ""
    if default_model_val:
        if "/" in default_model_val:
            configured_default = default_model_val.split("/", 1)[0].strip()
        else:
            configured_default = default_model_val

    if configured_default and normalize_provider_name(configured_default) == normalize_provider_name(existing_name):
        raise HTTPException(status_code=400, detail="Default provider cannot be deleted.")

    deleted = delete_config_tree(service, f"llm.providers.{existing_name}")
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_name}.")

    invalidate_agent_provider_options_cache()
    return {"status": "deleted", "name": existing_name}

@router.get("/providers/models")
async def list_dashboard_provider_models(
    request: Request,
    refresh: bool = False,
) -> dict[str, Any]:
    try:
        catalogs = await list_provider_model_catalogs(include_remote=refresh)
        service = get_request_config_service(request)
        default_model_val = str(service.get("llm.default_model", "") or "").strip()
        default_provider = ""
        if default_model_val:
            if "/" in default_model_val:
                default_provider = default_model_val.split("/", 1)[0].strip()
            else:
                default_provider = default_model_val

        if not default_provider:
            default_catalog = await list_provider_model_catalog(include_remote=refresh)
            default_provider = default_catalog.provider
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "default_provider": default_provider,
        "providers": [serialize_model_catalog(catalog) for catalog in catalogs],
    }


@router.post("/dashboard-api/providers/update-catalog")
async def update_providers_catalog() -> dict[str, str]:
    from agent.modules.providers import update_catalog_from_url
    success, message = await update_catalog_from_url()
    if not success:
        raise HTTPException(status_code=500, detail=message)
    invalidate_agent_provider_options_cache()
    return {"status": "success", "message": message}
