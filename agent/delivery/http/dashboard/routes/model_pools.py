"""Dashboard management for named priority model pools."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from agent.delivery.http.dashboard.routes.helpers.agents import invalidate_agent_provider_options_cache
from agent.delivery.http.dashboard.routes.helpers.deps import get_app_container, get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.providers import normalize_provider_name, provider_entries_from_flat_config
from agent.delivery.http.dashboard.routes.helpers.settings import delete_config_tree, update_config_settings
from agent.modules.providers import ModelPoolConfig, POOL_NAME_PATTERN, POOL_PREFIX

router = APIRouter(prefix="/dashboard-api/model-pools")


class CreateModelPoolBody(ModelPoolConfig):
    name: str = Field(pattern=POOL_NAME_PATTERN)


def _service(request: Request):
    return get_app_container(request).model_pool_service


def _require_pool(request: Request, name: str):
    if get_request_config_service(request).get(POOL_PREFIX + name) is None:
        raise HTTPException(404, f"Model pool not found: {name}.")
    return _service(request)


def _validate_members(request: Request, config: ModelPoolConfig) -> ModelPoolConfig:
    providers = provider_entries_from_flat_config(get_request_config_service(request).get_all())
    canonical = []
    for member in config.members:
        provider = providers.get(normalize_provider_name(member.provider))
        if provider is None:
            raise HTTPException(400, f"Unknown pool member provider: {member.provider}.")
        canonical.append({"provider": provider["name"], "model": member.model})
    return ModelPoolConfig.model_validate({**config.model_dump(), "members": canonical})


@router.get("")
async def list_model_pools(request: Request) -> dict[str, Any]:
    service = _service(request)
    config = get_request_config_service(request)
    return {"pools": [{"name": name, **pool.model_dump(mode="json"), "status": service.status(name),
                       "is_default": config.get("llm.default_model") == f"@pool/{name}"}
                      for name, pool in sorted(service.list_configs().items())]}


@router.post("", status_code=201)
async def create_model_pool(body: CreateModelPoolBody, request: Request) -> dict[str, str]:
    config = get_request_config_service(request)
    if config.get(POOL_PREFIX + body.name) is not None:
        raise HTTPException(409, f"Model pool already exists: {body.name}.")
    pool = _validate_members(request, ModelPoolConfig.model_validate(body.model_dump(exclude={"name"})))
    update_config_settings(config, {POOL_PREFIX + body.name: pool.model_dump(mode="json")}, require_writable=True)
    invalidate_agent_provider_options_cache()
    return {"status": "created", "name": body.name}


@router.put("/{name}")
async def update_model_pool(name: str, body: ModelPoolConfig, request: Request) -> dict[str, str]:
    service = _require_pool(request, name)
    pool = _validate_members(request, body)
    update_config_settings(get_request_config_service(request), {POOL_PREFIX + name: pool.model_dump(mode="json")}, require_writable=True)
    service.reset(name)
    invalidate_agent_provider_options_cache()
    return {"status": "updated", "name": name}


@router.delete("/{name}")
async def delete_model_pool(name: str, request: Request) -> dict[str, str]:
    service = _require_pool(request, name)
    config = get_request_config_service(request)
    if config.get("llm.default_model") == f"@pool/{name}":
        raise HTTPException(409, "The default model pool cannot be deleted.")
    catalog = get_app_container(request).agent_catalog_service
    for card in catalog.list_agent_cards():
        if card.provider == "@pool" and card.model == name:
            raise HTTPException(409, f"Model pool is referenced by agent '{card.name}'.")
    delete_config_tree(config, POOL_PREFIX + name)
    service.reset(name)
    invalidate_agent_provider_options_cache()
    return {"status": "deleted", "name": name}


@router.post("/{name}/reset")
async def reset_model_pool(name: str, request: Request) -> dict[str, str]:
    _require_pool(request, name).reset(name)
    return {"status": "reset", "name": name}
