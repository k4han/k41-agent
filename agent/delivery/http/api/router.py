from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from agent.delivery.http.api.schemas import ChatRequest, ChatResponse, PairingCodeResponse
from agent.modules.agent_runtime.public import (
    build_run_params,
    run_agent,
    run_agent_full,
)
from agent.modules.users.application.pairing_handler import get_user_service
from agent.modules.users.domain.constants import Platform
from agent.modules.workflows.public import list_registered_workflows
from agent.shared.config import get_config_service

router = APIRouter(prefix="/api", tags=["agent"])


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


async def verify_admin_key(x_admin_api_key: str = Header(..., alias="X-Admin-Api-Key")):
    key = get_config_service().get_str("admin.api_key")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin API key not configured",
        )
    if x_admin_api_key != key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin API key",
        )


@router.post("/users/pairing-code", response_model=PairingCodeResponse)
async def generate_pairing_code(_: str = Depends(verify_admin_key)):
    user_service = get_user_service()
    pairing_code, user_id = await user_service.create_pairing_root_user_and_code()
    return PairingCodeResponse(user_id=str(user_id), pairing_code=pairing_code)
