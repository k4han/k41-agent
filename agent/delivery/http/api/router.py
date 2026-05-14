import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from agent.modules.admin_auth import get_current_admin
from agent.delivery.http.api.schemas import (
    ChatRequest,
    ChatResponse,
    PairingCodeResponse,
)
from agent.modules.agent_runtime import (
    build_run_params,
    run_agent,
    run_agent_full,
    run_agent_stream,
)
from agent.modules.providers import (
    list_provider_model_catalog,
    list_provider_model_catalogs,
    list_providers,
)
from agent.modules.users import Platform, get_pairing_service
from agent.modules.workflows import list_registered_workflows

router = APIRouter(
    prefix="/api",
    tags=["agent"],
    dependencies=[Depends(get_current_admin)],
)


def _request_to_run_params(request: ChatRequest) -> dict[str, object]:
    return build_run_params(
        platform=Platform.API,
        user_id=request.user_id,
        user_input=request.message,
        workflow=request.workflow,
        working_dir=request.working_dir,
        agent_name=request.agent_name or "default",
        provider=request.provider,
        model=request.model,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat_sync(request: ChatRequest):
    """Return the full response for a chat request."""

    params = _request_to_run_params(request)
    response = await run_agent_full(**params)
    return ChatResponse(
        response=response,
        thread_id=params["thread_id"],
        workflow=params["workflow"],
    )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Stream the response for a chat request."""

    params = _request_to_run_params(request)

    async def event_generator():
        async for chunk in run_agent(**params):
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/plain")


@router.post("/chat/events")
async def chat_events(request: ChatRequest):
    """Stream UI events for a chat request as newline-delimited JSON."""

    params = _request_to_run_params(request)

    async def event_generator():
        async for event in run_agent_stream(**params):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@router.get("/graphs")
async def list_graphs():
    return {"graphs": list_registered_workflows()}


def _serialize_provider(provider) -> dict[str, object]:
    return {
        "name": provider.name,
        "type": str(provider.provider_type),
        "default_model": provider.default_model,
        "models": list(provider.models),
        "enabled": provider.enabled,
    }


def _serialize_model_catalog(catalog) -> dict[str, object]:
    return {
        "provider": catalog.provider,
        "provider_type": catalog.provider_type,
        "default_model": catalog.default_model,
        "can_list_models": catalog.can_list_models,
        "models": [
            {
                "id": option.id,
                "label": option.label,
                "source": option.source,
            }
            for option in catalog.models
        ],
        "error": catalog.error,
    }


@router.get("/providers")
async def api_list_providers():
    try:
        providers = list_providers()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"providers": [_serialize_provider(provider) for provider in providers]}


@router.get("/providers/models")
async def api_list_provider_models(refresh: bool = False):
    try:
        catalogs = await list_provider_model_catalogs(include_remote=refresh)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"providers": [_serialize_model_catalog(catalog) for catalog in catalogs]}


@router.get("/providers/{provider_name}/models")
async def api_list_provider_models_for_provider(
    provider_name: str,
    refresh: bool = False,
):
    try:
        catalog = await list_provider_model_catalog(
            provider_name,
            include_remote=refresh,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_model_catalog(catalog)


@router.get("/health")
async def health():
    return {"status": "ok", "graphs": list_registered_workflows()}


@router.post("/users/pairing-code", response_model=PairingCodeResponse)
async def generate_pairing_code():
    pairing_service = get_pairing_service()
    pairing_code, user_id = await pairing_service.create_pairing_root_user_and_code()
    return PairingCodeResponse(user_id=str(user_id), pairing_code=pairing_code)
