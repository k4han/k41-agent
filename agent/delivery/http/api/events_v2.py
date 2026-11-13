"""Versioned HTTP transport for the LangGraph v3 event protocol."""

from __future__ import annotations

import json
from contextlib import aclosing

from fastapi import APIRouter, Depends, HTTPException, Request

from agent.delivery.http.common.streaming import ChatStreamingResponse, stream_chat_chunks

from agent.delivery.http.api.router import (
    STREAM_RESPONSE_HEADERS,
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
