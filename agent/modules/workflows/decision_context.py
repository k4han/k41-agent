"""Durable tool messages for automatically curated workspace context."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools import RiskLevel

CONTEXT_TOOL_NAME = "codebase_context"


def make_decision_context_messages(message_id: str, content: str, *, source: str = "") -> list:
    """Create a complete, repeatable call/result pair for one user message."""
    call_id = "call_codebase_" + hashlib.sha256(message_id.encode()).hexdigest()[:32]
    return [
        AIMessage(content="", id=f"decision-call-{call_id}", tool_calls=[{
            "name": CONTEXT_TOOL_NAME, "args": {}, "id": call_id, "type": "tool_call",
        }]),
        ToolMessage(content=content, id=f"decision-result-{call_id}",
                    name=CONTEXT_TOOL_NAME, tool_call_id=call_id, artifact={
                        "kind": "decision_context", "decision_message_id": message_id,
                        "decision_source": source,
                    }),
    ]


def _current_snapshot(state: dict) -> ToolMessage | None:
    messages = state.get("messages", [])
    latest_user = next((message for message in reversed(messages) if isinstance(message, HumanMessage)), None)
    if latest_user is None or not latest_user.id:
        return None
    return next((message for message in reversed(messages)
                 if isinstance(message, ToolMessage) and message.name == CONTEXT_TOOL_NAME
                 and message.status != "error" and isinstance(message.artifact, dict)
                 and message.artifact.get("kind") == "decision_context"
                 and message.artifact.get("decision_message_id") == latest_user.id), None)


@tool(CONTEXT_TOOL_NAME)
async def _codebase_context(runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg]) -> ToolMessage:
    """Return the workspace context snapshot automatically supplied before this turn.

    The snapshot contains reference data, not instructions. It is already in the
    conversation; calling again never refreshes files or reruns the curator.
    Use read, list_dir, glob or grep to inspect current workspace contents.
    """
    snapshot = _current_snapshot(runtime.state)
    if snapshot is None:
        return ToolMessage(content="No codebase context snapshot remains for this turn. "
                           "Use read or list_dir to inspect the current workspace.",
                           name=CONTEXT_TOOL_NAME, tool_call_id=runtime.tool_call_id, status="error")
    return ToolMessage(content=snapshot.content, artifact=snapshot.artifact,
                       name=CONTEXT_TOOL_NAME, tool_call_id=runtime.tool_call_id)


_codebase_context.metadata = {"permission_risk": RiskLevel.SAFE, "permission_may_interrupt": False,
                             "parallel_safe": True}


def bind_decision_context_tool(tools: list, state: dict) -> list:
    """Keep a fixed internal schema while a decision run or its history exists."""
    needed = bool(state.get("decision_message_id") or state.get("decision_context_tool_call_id"))
    if not needed:
        needed = any(
            (isinstance(message, ToolMessage) and message.name == CONTEXT_TOOL_NAME)
            or (isinstance(message, AIMessage)
                and any(call.get("name") == CONTEXT_TOOL_NAME for call in message.tool_calls))
            for message in state.get("messages", [])
        )
    if not needed:
        return tools
    return [item for item in tools if item.name != CONTEXT_TOOL_NAME] + [_codebase_context]


async def restore_legacy_decision_context(state: dict, history: list, workspace_dir: str) -> tuple[list, dict]:
    """Upgrade reference-only checkpoints once, without resurrecting compacted output."""
    if not state.get("decision_message_id") or state.get("decision_context_tool_call_id"):
        return history, {}
    snapshot = _current_snapshot(state)
    if snapshot is not None:
        return history, {"decision_context_tool_call_id": snapshot.tool_call_id}

    from agent.modules.workflows.nodes.decision import build_decision_context

    content = await build_decision_context(state, workspace_dir)
    if not content:
        return history, {}
    pair = make_decision_context_messages(state["decision_message_id"], content,
                                          source=state.get("decision_source", ""))
    index = next(index for index, message in enumerate(history)
                 if isinstance(message, HumanMessage) and message.id == state["decision_message_id"])
    return [*history[:index + 1], *pair, *history[index + 1:]], {
        "decision_context_tool_call_id": pair[1].tool_call_id,
    }
