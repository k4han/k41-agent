from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from agent.modules.admin_auth.public import get_current_admin
from agent.delivery.http.api.schemas import ChatRequest, ChatResponse, PairingCodeResponse
from agent.modules.agent_runtime.public import (
    build_run_params,
    run_agent,
    run_agent_full,
)
from agent.modules.users.public import Platform, get_pairing_service
from agent.modules.workflows.public import list_registered_workflows
from agent.shared.config import get_config_service

router = APIRouter(prefix="/api", tags=["agent"], dependencies=[Depends(get_current_admin)])

def _request_to_run_params(request: ChatRequest) -> dict[str, object]:
    return build_run_params(
        platform=Platform.API,
        user_id=request.user_id,
        user_input=request.message,
        workflow=request.workflow,
        working_dir=request.working_dir,
        agent_name=request.agent_name or "default",
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


@router.get("/graphs")
async def list_graphs():
    return {"graphs": list_registered_workflows()}


@router.get("/health")
async def health():
    return {"status": "ok", "graphs": list_registered_workflows()}

@router.post("/users/pairing-code", response_model=PairingCodeResponse)
async def generate_pairing_code():
    pairing_service = get_pairing_service()
    pairing_code, user_id = await pairing_service.create_pairing_root_user_and_code()
    return PairingCodeResponse(user_id=str(user_id), pairing_code=pairing_code)
