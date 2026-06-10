import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from agent.modules.admin_auth import get_current_admin
from agent.delivery.http.api.schemas import (
    ChatRequest,
    ChatResponse,
    EditChatRequest,
    GraphListResponse,
    HealthResponse,
    ModelCatalog,
    ModelCatalogListResponse,
    PairingCodeResponse,
    ProviderListResponse,
    ProviderSummary,
    ReconnectRequest,
)
from agent.delivery.http.api.mcp import router as mcp_router
from agent.modules.agent_runtime import (
    build_run_params,
    run_agent,
    run_agent_edit_stream,
    run_agent_full,
    run_agent_stream,
    get_chat_stream_manager,
)
from agent.modules.conversations import create_thread_id
from agent.modules.providers import (
    list_provider_model_catalog,
    list_provider_model_catalogs,
    list_providers,
)
from agent.modules.users import Platform, get_pairing_service
from agent.modules.workspaces import (
    ensure_workspace_ready,
    get_thread_workspace_ref,
    remember_thread_workspace_ref,
    resolve_workspace_ref,
)
from agent.modules.workflows import list_registered_workflows

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["agent"],
    dependencies=[Depends(get_current_admin)],
)

router.include_router(mcp_router)


def _request_to_run_params(request: ChatRequest) -> dict[str, object]:
    thread_id = request.thread_id
    if (
        request.new_thread
        and not thread_id
        and request.user_id == "dashboard"
        and request.workspace is None
    ):
        raise HTTPException(
            status_code=400,
            detail="Dashboard chats require a resolved workspace.",
        )
    if request.new_thread and not thread_id:
        thread_id = create_thread_id(
            platform=Platform.API,
            user_id=request.user_id,
        )

    params = {
        "platform": Platform.API,
        "user_id": request.user_id,
        "user_input": request.message,
        "workflow": request.workflow,
        "workspace": request.workspace,
        "agent_name": request.agent_name or "default",
        "provider": request.provider,
        "model": request.model,
        "resume": request.resume,
    }
    if request.resume_payload is not None:
        params["resume_payload"] = request.resume_payload.model_dump(exclude_none=True)
    if request.checkpoint_id:
        params["checkpoint_id"] = request.checkpoint_id
    if request.attachments:
        params["attachments"] = [
            attachment.model_dump() for attachment in request.attachments
        ]
    if thread_id:
        params["thread_id"] = thread_id
    try:
        return build_run_params(**params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _apply_workspace_to_run_params(
    request: ChatRequest,
    params: dict[str, object],
) -> None:
    thread_id = str(params.get("thread_id") or "")
    if not thread_id:
        return

    requested_workspace = request.workspace
    stored_workspace = None
    if requested_workspace is None and request.thread_id:
        try:
            stored_workspace = await get_thread_workspace_ref(request.thread_id)
        except Exception as exc:
            logger.debug(
                "Failed to load workspace for thread %s: %s",
                request.thread_id,
                exc,
            )

    effective_workspace = requested_workspace or stored_workspace
    if effective_workspace is not None:
        resolved = resolve_workspace_ref(effective_workspace)
        if resolved.backend in {"daytona", "modal"}:
            resolved = await ensure_workspace_ready(resolved, thread_id=thread_id)
        params["workspace"] = resolved
    else:
        resolved = resolve_workspace_ref(None)

    try:
        await remember_thread_workspace_ref(thread_id, resolved)
    except Exception as exc:
        logger.debug(
            "Failed to remember workspace for thread %s: %s",
            thread_id,
            exc,
        )


@router.post("/chat", response_model=ChatResponse)
async def chat_sync(request: ChatRequest):
    """Send a chat message and return the full agent response synchronously."""
    params = _request_to_run_params(request)
    await _apply_workspace_to_run_params(request, params)
    response = await run_agent_full(**params)
    return ChatResponse(
        response=response,
        thread_id=params["thread_id"],
        workflow=params["workflow"],
    )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Stream the agent response as plain text chunks."""
    params = _request_to_run_params(request)
    await _apply_workspace_to_run_params(request, params)

    async def event_generator():
        async for chunk in run_agent(**params):
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/plain")


@router.post("/chat/events")
async def chat_events(request: ChatRequest):
    """Stream UI events (thread created, message deltas, tool calls, etc.) as newline-delimited JSON."""
    params = _request_to_run_params(request)
    await _apply_workspace_to_run_params(request, params)
    created_thread = bool(request.new_thread and not request.thread_id)
    thread_id = str(params["thread_id"])

    manager = get_chat_stream_manager()
    session = await manager.get_or_create_session(thread_id, params, run_fn=run_agent_stream)

    async def event_generator():
        if created_thread:
            yield json.dumps(
                {"type": "thread_created", "thread_id": thread_id},
                ensure_ascii=False,
            ) + "\n"
        async for event in session.subscribe():
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@router.post("/chat/events/edit")
async def chat_events_edit(request: EditChatRequest):
    """Fork a thread from an edited user message and stream UI events as newline-delimited JSON."""
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Edited message cannot be empty.")

    try:
        params = build_run_params(
            platform=Platform.API,
            user_id=request.user_id,
            user_input=request.message,
            thread_id=request.thread_id,
            workflow=request.workflow,
            workspace=request.workspace,
            agent_name=request.agent_name or "default",
            provider=request.provider,
            model=request.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    params["message_index"] = request.message_index
    params["source_checkpoint_id"] = request.source_checkpoint_id
    await _apply_workspace_to_run_params(request, params)

    manager = get_chat_stream_manager()
    session = await manager.get_or_create_session(
        request.thread_id,
        params,
        run_fn=run_agent_edit_stream,
    )

    async def event_generator():
        async for event in session.subscribe():
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@router.post("/chat/events/reconnect")
async def chat_events_reconnect(request: ReconnectRequest):
    """Reconnect to an active chat stream and continue receiving UI events."""
    thread_id = request.thread_id
    manager = get_chat_stream_manager()
    session = await manager.get_session(thread_id)

    if not session:
        # If no active session found (already finished), return empty stream
        # so client immediately finishes and loads thread history from database
        async def empty_generator():
            return
            yield  # noqa: makes this an async generator
        return StreamingResponse(empty_generator(), media_type="application/x-ndjson")

    async def event_generator():
        async for event in session.subscribe():
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@router.get("/graphs", response_model=GraphListResponse)
async def list_graphs():
    """List all registered workflow graphs."""
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
                "context_window": option.context_window,
                "input_types": list(option.input_types)
                if option.input_types is not None
                else None,
            }
            for option in catalog.models
        ],
        "error": catalog.error,
    }


@router.get("/providers", response_model=ProviderListResponse)
async def api_list_providers():
    """List all configured LLM providers and their available models."""
    try:
        providers = list_providers()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"providers": [_serialize_provider(provider) for provider in providers]}


@router.get("/providers/models", response_model=ModelCatalogListResponse)
async def api_list_provider_models(refresh: bool = False):
    """List model catalogs for all configured providers. Use refresh=true to re-fetch from upstream."""
    try:
        catalogs = await list_provider_model_catalogs(include_remote=refresh)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"providers": [_serialize_model_catalog(catalog) for catalog in catalogs]}


@router.get("/providers/{provider_name}/models", response_model=ModelCatalog)
async def api_list_provider_models_for_provider(
    provider_name: str,
    refresh: bool = False,
):
    """List model catalog for a specific provider. Use refresh=true to re-fetch from upstream."""
    try:
        catalog = await list_provider_model_catalog(
            provider_name,
            include_remote=refresh,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_model_catalog(catalog)


@router.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint returning service status and registered graphs."""
    return {"status": "ok", "graphs": list_registered_workflows()}


@router.post("/users/pairing-code", response_model=PairingCodeResponse)
async def generate_pairing_code():
    """Generate a one-time pairing code for client authentication."""
    pairing_service = get_pairing_service()
    pairing_code, user_id = await pairing_service.create_pairing_root_user_and_code()
    return PairingCodeResponse(user_id=str(user_id), pairing_code=pairing_code)
