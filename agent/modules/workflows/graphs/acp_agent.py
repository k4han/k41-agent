"""ACP agent graph: an external agent runs the loop; this graph streams and records it.

    START -> acp_prepare -> acp_turn -> (permission pending?) acp_permission -> acp_turn ... -> END

Node writes are discarded when a node interrupts. ``acp_turn`` therefore commits
the transcript at every permission boundary, and only ``acp_permission`` interrupts.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
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
    AcpTurn,
    PendingHandoff,
    ToolOutput,
    TurnRequest,
    describe_failure,
    get_acp_service,
    not_installed_notice,
    tool_output,
)
from agent.modules.browser import BROWSER_HANDOFF_WAIT_KEY, BrowserHandoffResume, abandon_thread_handoffs
from agent.modules.tools import AcpPermissionResume, ToolResolver, coding_message_for_model, get_thread_id, is_browser_tool
from agent.modules.workflows.checkpoint import get_checkpointer
from agent.modules.workflows.constants import ACP_AGENT_GRAPH_TYPE
from agent.modules.workflows.nodes.tool import tool_node
from agent.modules.workflows.prompt_builders import resolve_prompt_variables
from agent.modules.workflows.registry import GraphRegistry
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.acp import AcpState

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


class CardToolProvider:
    """Run a card's tools through the regular tool node, outside any graph run.

    Reusing the node keeps policy, tool configs, coding permissions and output
    retention identical to other agents. No node is active here, so a tool cannot
    interrupt for approval and ``approval_supported`` stays off: "ask" rules fail
    closed. The agent's own approval request covers the call as a whole. A browser
    handoff waits on the turn instead, and ``acp_permission`` asks the user for it.
    """

    def __init__(self, context: WorkflowContext, config: RunnableConfig, turn_id: str) -> None:
        self._context = context
        self._turn_id = turn_id
        self._can_interrupt = bool(config.get("configurable", {}).get("acp_can_interrupt"))
        # Only what tools read. The rest of a node's config belongs to that graph run.
        self._config: RunnableConfig = {
            "configurable": {
                "thread_id": get_thread_id(config),
                "approval_supported": False,
                BROWSER_HANDOFF_WAIT_KEY: self._wait_for_handoff,
            },
            "metadata": dict(config.get("metadata") or {}),
        }
        self._browser_lock = asyncio.Lock()

    async def _wait_for_handoff(self, payload: dict[str, Any]) -> str:
        turn = get_acp_service().turn(self._turn_id)
        return await turn.wait_for_handoff(payload) if turn is not None else "cancel"

    async def _resolve(self):
        resolver = ToolResolver()
        tools = await resolver.aresolve_for_agent(
            self._context.get_agent_name(),
            override_tool_names=self._context.get_allowed_tool_names(),
        )
        return resolver.for_workspace(tools, self._context.get_workspace())

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": convert_to_openai_tool(tool)["function"]["parameters"],
            }
            for tool in await self._resolve()
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        # Checked here because the node restores control tools named by a pending call.
        if name not in {tool.name for tool in await self._resolve()}:
            return ToolOutput(f"Tool '{name}' is not available to this agent.", is_error=True)
        if name == "browser_handoff" and not self._can_interrupt:
            return ToolOutput("Nobody can take over the browser from this surface. Use a dashboard conversation.", is_error=True)
        if not is_browser_tool(name):
            return await self._invoke(name, arguments)
        # One browser call at a time, as tool_node enforces for a model's batch.
        async with self._browser_lock:
            try:
                return await self._invoke(name, arguments)
            finally:
                turn = get_acp_service().turn(self._turn_id)
                if name == "browser_handoff" and turn is not None:
                    # The transcript names this call acp_other, so the dashboard cannot recognise its result.
                    turn.add_event({"type": "browser_handoff_settled"})

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        call = {"id": uuid.uuid4().hex, "name": name, "args": arguments, "type": "tool_call"}
        state = {"messages": [AIMessage(content="", id=uuid.uuid4().hex, tool_calls=[call])]}
        result = await tool_node(state, self._config, Runtime(context=self._context))
        message = coding_message_for_model(result["messages"][-1])
        return tool_output(message.content, is_error=message.status == "error")


async def acp_prepare_node(state: AcpState, config: RunnableConfig, runtime: Runtime[WorkflowContext]) -> dict[str, Any]:
    """Name this turn and end any turn the thread still has running."""
    service = get_acp_service()
    thread_id = get_thread_id(config)
    await service.supersede(thread_id)
    # A handoff left by a restart has no tool call waiting on it anymore.
    await abandon_thread_handoffs(thread_id)
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
        tools=CardToolProvider(ctx, config, turn_id) if agent_config.tools else None,
        commit_stopped=_commit_stopped_turn,
    ))


async def _commit_stopped_turn(turn: AcpTurn) -> None:
    """Record a turn stopped during a handoff and release the graph paused on that handoff.

    No run can do this: the handoff's request is gone, so the dashboard has nothing
    left to answer. The graph is moved directly, the way forking moves one.
    """
    from agent.modules.agent_runtime import get_active_session_registry
    from agent.modules.workflows import get_workflow_graph, make_run_config

    graph = get_workflow_graph(ACP_AGENT_GRAPH_TYPE)
    config = make_run_config(thread_id=turn.thread_id)
    async with get_active_session_registry().wait_for_thread_mutation(turn.thread_id):
        state = await graph.aget_state(config)
        # A new message may have replaced this turn while the agent acknowledged the cancel.
        if state.values.get("acp_turn_id") == turn.turn_id and "acp_permission" in state.next:
            values = {"messages": turn.messages(notice=turn.result_notice()), "acp_pending": False}
            updated = await graph.aupdate_state(config, values, as_node="acp_turn")
            await graph.aupdate_state(updated, None, as_node=END)
    turn.committed = True


async def acp_turn_node(state: AcpState, config: RunnableConfig, runtime: Runtime[WorkflowContext]) -> dict[str, Any]:
    """Drive the turn until it ends or needs a human, then commit its transcript."""
    service = get_acp_service()
    writer = get_stream_writer()
    turn_id = str(state.get("acp_turn_id") or "")
    turn = service.turn(turn_id)
    if turn is None:
        if service.was_started(turn_id) or state.get("acp_boot_id") != service.boot_id:
            # The prompt may already have run, so it must not be sent again.
            await abandon_thread_handoffs(get_thread_id(config))
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
    """Pause for one decision and hand it to whatever in the turn waits for it."""
    # Being here again means the person answered, which also dismisses an expired handoff.
    await abandon_thread_handoffs(get_thread_id(config), expired_only=True)
    turn = get_acp_service().turn(str(state.get("acp_turn_id") or ""))
    pending = turn.next_pending() if turn is not None else None
    if pending is None:
        # The turn ended while paused, or the server restarted. acp_turn reports it.
        return {"acp_pending": False}
    while True:
        response = interrupt(pending.interrupt_value())
        if isinstance(pending, PendingHandoff):
            try:
                handoff = BrowserHandoffResume.model_validate(response)
            except ValidationError:
                continue
            if handoff.handoff_id != pending.request_id:
                continue
            turn.answer_handoff(pending.request_id, handoff.decision)
            return {"acp_pending": False}
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
