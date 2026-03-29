from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent.delivery.http.api.schemas import ChatRequest, ChatResponse
from agent.modules.agent_runtime.public import (
    build_run_params,
    run_agent,
    run_agent_full,
)
from agent.modules.workflows.public import list_registered_workflows

router = APIRouter(prefix="/api", tags=["agent"])


def _request_to_run_params(request: ChatRequest) -> dict[str, object]:
    return build_run_params(
        platform="api",
        user_id=request.user_id,
        user_input=request.message,
        workflow=request.workflow,
        service_type=request.service_type,
        working_dir=request.working_dir,
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
