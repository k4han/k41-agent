# agent/adapters/fastapi/router.py

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent.adapters.base           import BaseAdapter
from agent.adapters.fastapi.schemas import ChatRequest, ChatResponse
from agent.core.runner             import run_agent, run_agent_full
from agent.registry                import GraphRegistry

router = APIRouter(prefix="/api", tags=["agent"])


class FastAPIAdapter(BaseAdapter):
    async def handle(self, request: ChatRequest):
        return self.normalize(
            platform=    "api",
            user_id=      request.user_id,
            user_input=   request.message,
            workflow=     request.workflow,
            service_type= request.service_type,
            working_dir=  request.working_dir,
        )


adapter = FastAPIAdapter()


@router.post("/chat", response_model=ChatResponse)
async def chat_sync(request: ChatRequest):
    """Chat thông thường — trả về response đầy đủ."""
    params    = await adapter.handle(request)
    response  = await run_agent_full(**params)
    return ChatResponse(
        response=response,
        thread_id=params["thread_id"],
        workflow=params["workflow"],
    )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Chat với streaming response."""
    params = await adapter.handle(request)

    async def event_generator():
        async for chunk in run_agent(**params):
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/plain")


@router.get("/graphs")
async def list_graphs():
    """Liệt kê tất cả graphs đã đăng ký."""
    return {"graphs": list(GraphRegistry.all().keys())}


@router.get("/health")
async def health():
    return {"status": "ok", "graphs": list(GraphRegistry.all().keys())}
