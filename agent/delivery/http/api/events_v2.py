"""Versioned HTTP transport for the LangGraph v3 event protocol."""

from __future__ import annotations

import json
import asyncio
from contextlib import aclosing

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agent.delivery.http.common.streaming import ChatStreamingResponse, stream_chat_chunks

from agent.delivery.http.api.router import (
    STREAM_RESPONSE_HEADERS,
    check_scheduled_chat,
    prepare_edit_params,
    prepare_run_params,
)
from agent.delivery.http.api.schemas import (
    ChatRequest,
    EditChatRequest,
    ReconnectRequest,
)
from agent.modules.admin_auth import get_current_admin
from agent.modules.agent_runtime import (
    get_chat_stream_manager,
    protocol_event,
    run_agent_edit_protocol_stream,
    run_agent_protocol_stream,
)
from agent.shared.thread_ids import resolve_thread_id
from agent.modules.agent_runtime.async_subagents import get_async_subagent_service, active_async_subagent_service
from agent.shared.async_subagents import request_key
from agent.modules.tools import HumanResumePayload

router = APIRouter(
    prefix="/v2/api", tags=["agent-events"], dependencies=[Depends(get_current_admin)]
)


def stream_response(request: Request, session=None, *, after_seq=0):
    async def generate():
        if session is not None:
            async with aclosing(session.subscribe(after_seq=after_seq)) as events:
                async for event in events:
                    yield json.dumps(event, ensure_ascii=False) + "\n"

    return ChatStreamingResponse(
        # Empty NDJSON lines are ignored by clients and do not enter replay logs.
        stream_chat_chunks(generate(), request, heartbeat="\n"),
        media_type="application/x-ndjson",
        headers={
            **STREAM_RESPONSE_HEADERS,
            "X-Stream-Version": "v3",
            **(
                {"X-Run-ID": session.run_id, "X-Thread-ID": session.thread_id}
                if session
                else {}
            ),
        },
    )


@router.post("/chat/events")
async def chat_events(request: ChatRequest, http_request: Request, admin: str = Depends(get_current_admin)):
    params = await prepare_run_params(request, browser_owner_id=f"admin:{admin}")
    thread_id = str(params["thread_id"])
    from agent.modules.scheduler.executions import resume_run
    managed = active_async_subagent_service()
    scheduled = await check_scheduled_chat(thread_id, f"admin:{admin}", allow_managed_resume=True)
    if scheduled:
        if scheduled["status"] != "waiting" or request.resume_payload is None:
            raise HTTPException(409, detail="This scheduled run is active. Resume its pending request or cancel it before starting another run.")
        payload = request.resume_payload.model_dump(exclude_none=True)
        interrupts = scheduled.get("interrupts", [])
        if payload.get("action") == "browser_handoff":
            interrupts = [item for item in interrupts if isinstance(item["value"], dict)
                and item["value"].get("handoff_id") == payload.get("handoff_id")]
        if len(interrupts) != 1:
            raise HTTPException(409, detail="The scheduled run requires an exact pending interrupt.")
        manager = get_chat_stream_manager()
        previous = await manager.get_session(thread_id)
        try:
            await resume_run(managed, scheduled["run_id"], interrupts[0]["id"], payload, f"admin:{admin}")
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from None
        for _ in range(100):
            session = await manager.get_session(thread_id)
            if session is not None and session is not previous:
                return stream_response(http_request, session)
            await asyncio.sleep(0.05)
        raise HTTPException(503, detail="The scheduled response was saved and is queued. Reconnect to follow its execution.")
    subagents = active_async_subagent_service()
    if request.checkpoint_id and subagents and await subagents.repository.has_work(thread_id):
        raise HTTPException(409, detail="Stop async tasks before rewinding this conversation.")
    session = await get_chat_stream_manager().get_or_create_session(
        thread_id, params, run_fn=run_agent_protocol_stream
    )
    if request.new_thread and not request.thread_id:
        await session.push_event(
            protocol_event(
                "custom:k41",
                {
                    "type": "thread_created",
                    "thread_id": thread_id,
                    "projection": "ui",
                },
            )
        )
    return stream_response(http_request, session)


@router.post("/chat/events/edit")
async def chat_events_edit(
    request: EditChatRequest, http_request: Request, admin: str = Depends(get_current_admin)
):
    params = await prepare_edit_params(request, admin)
    subagents = active_async_subagent_service()
    if subagents and await subagents.repository.has_work(str(params["thread_id"])):
        raise HTTPException(409, detail="Stop async tasks before editing this conversation.")
    session = await get_chat_stream_manager().get_or_create_session(
        str(params["thread_id"]),
        params,
        run_fn=run_agent_edit_protocol_stream,
    )
    return stream_response(http_request, session)


@router.post("/chat/events/reconnect")
async def reconnect(request: ReconnectRequest, http_request: Request):
    session = await get_chat_stream_manager().get_session(
        resolve_thread_id(request.thread_id)
    )
    if (
        session is not None
        and request.run_id is not None
        and session.run_id != request.run_id
    ):
        raise HTTPException(
            409,
            detail={
                "code": "run_replaced",
                "message": "The stream belongs to a different run.",
            },
        )
    return stream_response(http_request, session, after_seq=request.after_seq or 0)


@router.get("/chat/{thread_id}/subagents/{delegation_id}")
async def subagent_history(thread_id: str, delegation_id: str):
    from agent.modules.agent_runtime import get_subagent_history

    result = await get_subagent_history(resolve_thread_id(thread_id), delegation_id)
    if result is None:
        raise HTTPException(404, detail="Subagent history not found.")
    return result


class AsyncTaskUpdateRequest(BaseModel):
    instructions: str = Field(min_length=1, max_length=100_000)
    request_id: str = Field(min_length=1, max_length=128)


class AsyncTaskResumeRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    interrupt_id: str = Field(min_length=1, max_length=128)
    payload: HumanResumePayload


async def _task_operation(operation):
    try:
        return await operation
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.get("/chat/{thread_id}/async-tasks")
async def list_async_tasks(thread_id: str, status: str | None = None, limit: int = 50, offset: int = 0):
    from agent.shared.async_subagents import LIVE_STATUSES, TERMINAL_STATUSES

    if not 1 <= limit <= 100 or offset < 0 or (status and status not in LIVE_STATUSES | TERMINAL_STATUSES | {"active"}):
        raise HTTPException(422, detail="Invalid status or pagination.")
    return await get_async_subagent_service().repository.list_tasks(resolve_thread_id(thread_id),
        status=status, limit=limit, offset=offset)


@router.get("/chat/{thread_id}/async-tasks/{task_id}")
async def get_async_task(thread_id: str, task_id: str):
    return await _task_operation(get_async_subagent_service().get(resolve_thread_id(thread_id), task_id))


@router.post("/chat/{thread_id}/async-tasks/{task_id}/updates", status_code=202)
async def update_async_task(thread_id: str, task_id: str, body: AsyncTaskUpdateRequest):
    parent = resolve_thread_id(thread_id)
    return await _task_operation(get_async_subagent_service().update(parent, task_id, body.instructions,
        request_key("http-update", parent, task_id, body.request_id)))


@router.post("/chat/{thread_id}/async-tasks/{task_id}/cancel", status_code=202)
async def cancel_async_task(thread_id: str, task_id: str):
    return await _task_operation(get_async_subagent_service().cancel(resolve_thread_id(thread_id), task_id))


@router.post("/chat/{thread_id}/async-tasks/{task_id}/resume", status_code=202)
async def resume_async_task(thread_id: str, task_id: str, body: AsyncTaskResumeRequest, admin: str = Depends(get_current_admin)):
    return await _task_operation(get_async_subagent_service().resume(resolve_thread_id(thread_id), task_id,
        body.run_id, body.interrupt_id, body.payload.model_dump(exclude_none=True), browser_owner_id=f"admin:{admin}"))
