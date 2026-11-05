"""ACP agent graph: an external agent runs the loop; this graph streams and records it.

    START -> acp_prepare -> acp_turn -> (permission pending?) acp_permission -> acp_turn ... -> END

Node writes are discarded when a node interrupts. ``acp_turn`` therefore commits
the transcript at every permission boundary, and only ``acp_permission`` interrupts.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from agent.modules.acp import (
    ACP_LIVE_EVENT,
    ACP_LIVE_KWARG,
    SIGN_IN_NOTICE,
    STOP_TURN_OPTION_ID,
    AcpError,
    AcpNotInstalled,
    AcpRpcError,
    TurnRequest,
    describe_failure,
    get_acp_service,
    not_installed_notice,
)
from agent.modules.tools import AcpPermissionResume, get_thread_id
from agent.modules.workflows.checkpoint import get_checkpointer
from agent.modules.workflows.constants import ACP_AGENT_GRAPH_TYPE
from agent.modules.workflows.prompt_builders import resolve_prompt_variables
from agent.modules.workflows.registry import GraphRegistry
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.acp import AcpState

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

TURN_LOST_NOTICE = (
    "This agent turn was interrupted by a restart and cannot continue. "
    "Any actions it already took were not undone. Send your message again to continue."
)
REMOTE_WORKSPACE_NOTICE = "ACP agents can only run in a local workspace. Switch this conversation to a local workspace first."


def _notice_message(turn_id: str, label: str, text: str) -> AIMessage:
    return AIMessage(
        content=text,
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{turn_id}:{label}")),
        additional_kwargs={ACP_LIVE_KWARG: True},
    )


def _emit_notice(writer: Any, text: str) -> None:
    writer({"type": ACP_LIVE_EVENT, "event": {"type": "message", "content": text}})
    writer({"type": ACP_LIVE_EVENT, "event": {"type": "final", "content": text, "already_streamed": True}})


def _end_with_notice(writer: Any, turn_id: str, label: str, text: str) -> dict[str, Any]:
    _emit_notice(writer, text)
    return {"messages": [_notice_message(turn_id, label, text)], "acp_pending": False}


def _last_human_message(state: AcpState) -> HumanMessage | None:
    return next((message for message in reversed(state["messages"]) if isinstance(message, HumanMessage)), None)


def prompt_blocks(message: HumanMessage, *, images_supported: bool = True) -> list[dict[str, Any]]:
    """Convert a stored user message to ACP content blocks."""
    if isinstance(message.content, str):
        return [{"type": "text", "text": message.content}]
    blocks: list[dict[str, Any]] = []
    for part in message.content:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
        elif part.get("type") == "text":
            blocks.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image" and images_supported and part.get("base64"):
            blocks.append({"type": "image", "data": part["base64"], "mimeType": part.get("mime_type") or "image/png"})
    return blocks


async def acp_prepare_node(state: AcpState, config: RunnableConfig, runtime: Runtime[WorkflowContext]) -> dict[str, Any]:
    """Name this turn and end any turn the thread still has running."""
    service = get_acp_service()
    thread_id = get_thread_id(config)
    await service.supersede(thread_id)
    user_message = _last_human_message(state)
    message_id = user_message.id if user_message is not None else uuid.uuid4().hex
    return {
        "acp_turn_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{thread_id}:{message_id}")),
        "acp_boot_id": service.boot_id,
        "acp_pending": False,
    }


async def _start_turn(state: AcpState, config: RunnableConfig, runtime: Runtime[WorkflowContext], turn_id: str):
    from agent.modules.agents import get_catalog_service
    from agent.modules.prompt_variables import get_runtime_prompt_variable_values

    ctx = runtime.context
    workspace = ctx.get_workspace()
    agent_config = get_catalog_service().get_agent(ctx.get_agent_name())
    if agent_config is None or agent_config.acp is None:
        raise AcpError(f"Agent '{ctx.get_agent_name()}' has no ACP settings.")
    settings = agent_config.acp
    thread_id = get_thread_id(config)
    cwd_path = Path(ctx.get_working_dir()).resolve()
    # Thread workspaces are created on first use, and no K41 tool runs here to do it.
    cwd_path.mkdir(parents=True, exist_ok=True)
    cwd = str(cwd_path)
    user_message = _last_human_message(state)

    async def system_prompt() -> str:
        template = agent_config.system_prompt or ""
        if not template.strip():
            return ""
        return resolve_prompt_variables(template, await get_runtime_prompt_variable_values(workspace, thread_id=thread_id))

    return await get_acp_service().begin_turn(TurnRequest(
        turn_id=turn_id,
        thread_id=thread_id,
        agent_id=settings.agent,
        cwd=cwd,
        blocks=prompt_blocks(user_message) if user_message is not None else [],
        session_id=str(state.get("acp_session_id") or ""),
        session_cwd=str(state.get("acp_session_cwd") or ""),
        auto_allow=settings.permission_mode == "auto_allow",
        can_interrupt=bool(config.get("configurable", {}).get("acp_can_interrupt")),
        config_options={**settings.config_options, **(ctx.acp_config_options or {})},
        system_prompt=system_prompt,
    ))


async def acp_turn_node(state: AcpState, config: RunnableConfig, runtime: Runtime[WorkflowContext]) -> dict[str, Any]:
    """Drive the turn until it ends or needs a human, then commit its transcript."""
    service = get_acp_service()
    writer = get_stream_writer()
    turn_id = str(state.get("acp_turn_id") or "")
    turn = service.turn(turn_id)
    if turn is None:
        if service.was_started(turn_id) or state.get("acp_boot_id") != service.boot_id:
            # The prompt may already have run, so it must not be sent again.
            return _end_with_notice(writer, turn_id, "lost", TURN_LOST_NOTICE)
        if runtime.context.get_workspace().backend != "local":
            return _end_with_notice(writer, turn_id, "remote", REMOTE_WORKSPACE_NOTICE)
        try:
            turn = await _start_turn(state, config, runtime, turn_id)
        except AcpNotInstalled as exc:
            return _end_with_notice(writer, turn_id, "setup", str(exc) or not_installed_notice("agent"))
        except AcpRpcError as exc:
            text = SIGN_IN_NOTICE if exc.auth_required else describe_failure(exc)
            return _end_with_notice(writer, turn_id, "setup", text)
        except AcpError as exc:
            return _end_with_notice(writer, turn_id, "setup", describe_failure(exc))
    turn.attached = True
    try:
        while True:
            for event in turn.take_events():
                writer({"type": ACP_LIVE_EVENT, "event": event})
            if turn.done or turn.next_pending() is not None:
                break
            await turn.wait_for_progress()
    except BaseException:
        # Cancelled runs were stopped or superseded. A client disconnect does not
        # reach here, because the run outlives its subscribers.
        turn.attached = False
        turn.cancel()
        raise
    update: dict[str, Any] = {
        "acp_session_id": turn.session_id,
        "acp_session_cwd": str(Path(runtime.context.get_working_dir()).resolve()),
    }
    if not turn.done:
        turn.attached = False
        update.update(messages=turn.messages(), acp_pending=True)
        return update
    for event in turn.take_events():
        writer({"type": ACP_LIVE_EVENT, "event": event})
    notice = turn.result_notice()
    if notice:
        writer({"type": ACP_LIVE_EVENT, "event": {"type": "message", "content": notice}})
    final_text = turn.final_text(notice)
    if final_text:
        writer({"type": ACP_LIVE_EVENT, "event": {"type": "final", "content": final_text, "already_streamed": True}})
    update.update(messages=turn.messages(notice=notice), acp_pending=False)
    turn.committed = True
    return update


async def acp_permission_node(state: AcpState, config: RunnableConfig) -> dict[str, Any]:
    """Pause for one permission decision and hand it to the waiting agent."""
    turn = get_acp_service().turn(str(state.get("acp_turn_id") or ""))
    pending = turn.next_pending() if turn is not None else None
    if pending is None:
        # The turn ended while paused, or the server restarted. acp_turn reports it.
        return {"acp_pending": False}
    while True:
        response = interrupt(pending.interrupt_value())
        try:
            decision = AcpPermissionResume.model_validate(response)
        except ValidationError:
            continue
        # A resume value for another request, or for an option that was not
        # offered, authorizes nothing, so the question is asked again.
        if decision.request_id != pending.request_id:
            continue
        if decision.option_id == STOP_TURN_OPTION_ID:
            turn.cancel()
            return {"acp_pending": False}
        if turn.answer(pending.request_id, decision.option_id):
            return {"acp_pending": False}
        if turn.next_pending() is None or turn.done:
            return {"acp_pending": False}


def _after_turn(state: AcpState) -> str:
    return "acp_permission" if state.get("acp_pending") else END


def compile_acp_agent_graph(checkpointer):
    graph = StateGraph(AcpState, context_schema=WorkflowContext)
    graph.add_node("acp_prepare", acp_prepare_node)
    graph.add_node("acp_turn", acp_turn_node)
    graph.add_node("acp_permission", acp_permission_node)
    graph.add_edge(START, "acp_prepare")
    graph.add_edge("acp_prepare", "acp_turn")
    graph.add_conditional_edges("acp_turn", _after_turn, {"acp_permission": "acp_permission", END: END})
    graph.add_edge("acp_permission", "acp_turn")
    return graph.compile(checkpointer=checkpointer)


def build_acp_agent_graph(checkpointer=None, *, graph_name: str = ACP_AGENT_GRAPH_TYPE) -> None:
    if checkpointer is None:
        checkpointer = get_checkpointer()
    GraphRegistry.register(
        graph_name,
        compile_acp_agent_graph(checkpointer),
        description="an external coding agent connected through the Agent Client Protocol",
        routeable=False,
    )


__all__ = ["build_acp_agent_graph", "compile_acp_agent_graph", "prompt_blocks"]
