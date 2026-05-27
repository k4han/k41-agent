from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from agent.delivery.http.dashboard.routes.shared import (
    _delete_config_tree,
    _get_config_service,
    _normalize_provider_name,
    _provider_config_name,
    _provider_entries_from_flat_config,
    _provider_type_from_body,
    _serialize_model_catalog,
    _settings_payload,
    _update_config_settings,
    _validate_provider_name,
)
from agent.modules.providers import (
    list_provider_model_catalog,
    list_provider_model_catalogs,
)


router = APIRouter()


@router.get("/dashboard-api/config")
async def get_dashboard_config(request: Request) -> dict[str, Any]:
    return _settings_payload(request, include_provider_settings=False)


@router.get("/dashboard-api/providers")
async def get_dashboard_providers(request: Request) -> dict[str, Any]:
    return _settings_payload(request, include_provider_settings=True)


class CreateProviderBody(BaseModel):
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    base_url: str = ""


@router.post("/dashboard-api/providers")
async def create_dashboard_provider(
    body: CreateProviderBody,
    request: Request,
) -> dict[str, str]:
    service = _get_config_service(request)
    provider_name = _validate_provider_name(body.name)
    provider_type = _provider_type_from_body(body.type)

    providers = _provider_entries_from_flat_config(service.get_all())
    if _normalize_provider_name(provider_name) in providers:
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

    values: dict[str, Any | None] = {
        f"llm.providers.{provider_name}.type": provider_type,
        f"llm.providers.{provider_name}.api_key": api_key,
        f"llm.providers.{provider_name}.default_model": "",
        f"llm.providers.{provider_name}.models": [],
        f"llm.providers.{provider_name}.enabled": True,
    }
    if provider_type == "openai_compatible":
        values[f"llm.providers.{provider_name}.base_url"] = base_url

    _update_config_settings(service, values, require_writable=True)
    return {"status": "created", "name": provider_name, "type": provider_type}


@router.delete("/dashboard-api/providers/{provider_name}")
async def delete_dashboard_provider(
    provider_name: str,
    request: Request,
) -> dict[str, str]:
    service = _get_config_service(request)
    existing_name = _provider_config_name(service, provider_name)
    if existing_name is None:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_name}.")

    configured_default = str(service.get("llm.default_provider", "") or "").strip()
    if configured_default and _normalize_provider_name(configured_default) == _normalize_provider_name(existing_name):
        raise HTTPException(status_code=400, detail="Default provider cannot be deleted.")

    deleted = _delete_config_tree(service, f"llm.providers.{existing_name}")
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_name}.")

    return {"status": "deleted", "name": existing_name}

@router.get("/providers/models")
async def list_dashboard_provider_models(refresh: bool = False) -> dict[str, Any]:
    try:
        catalogs = await list_provider_model_catalogs(include_remote=refresh)
        default_catalog = await list_provider_model_catalog(include_remote=refresh)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "default_provider": default_catalog.provider,
        "providers": [_serialize_model_catalog(catalog) for catalog in catalogs],
    }
