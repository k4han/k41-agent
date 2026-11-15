"""Non-blocking delegation and durable task management tools."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from agent.modules.agent_runtime.async_subagents import get_async_subagent_service
from agent.shared.async_subagents import is_subagent_run, request_key, task_reference
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.permissions.risk import RiskLevel
from agent.modules.tools.result import ToolError, ToolErrorCode

Runtime = Annotated[ToolRuntime[Any, Any], InjectedToolArg]
TaskStatus = Literal["pending", "running", "waiting", "completed", "failed", "cancelled", "timed_out"]


def _parent(runtime) -> str:
    if is_subagent_run(runtime.config):
        raise ToolError(ToolErrorCode.PERMISSION_DENIED, "Subagents cannot delegate to other agents.")
    configurable = runtime.config.get("configurable", {})
    if not configurable.get("async_subagents_supported") or not configurable.get("thread_id"):
        raise ToolError(ToolErrorCode.PERMISSION_DENIED, "Async subagents require a dashboard conversation.")
    return str(configurable["thread_id"])


async def _call(operation):
    try:
        return await operation
    except PermissionError as exc:
        raise ToolError(ToolErrorCode.PERMISSION_DENIED, str(exc)) from exc
    except LookupError as exc:
        raise ToolError(ToolErrorCode.NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc


def _result(runtime, value: dict, *, attach=False):
    tasks = value.get("tasks", [value])
    references = {task["task_id"]: task_reference(task) for task in tasks}
    artifact = {"subagent": task_reference(value)} if attach else None
    return Command(update={"messages": [ToolMessage(content=json.dumps(value, ensure_ascii=False),
        tool_call_id=runtime.tool_call_id, artifact=artifact)], "async_tasks": references})


def _registration(risk):
    return register_tool(risk=risk, may_interrupt=False, category=ToolCategory.AGENT,
        capabilities=[ToolCapability.ASYNC_ONLY, ToolCapability.REQUIRES_THREAD],
        tags=["subagent", "delegation", "async"])


@_registration(RiskLevel.MUTATING)
@tool
async def start_async_task(task: str, sub_agent: str, runtime: Runtime) -> Command:
    """Start a subagent in the background and immediately return its task_id.

    Use an exact sub_agent name from the callable agents catalog and a self-contained task.
    Continue independent work or return control to the user after launching.
    Results arrive automatically. Never immediately poll check_async_task in a loop.
    """
    _parent(runtime)
    value = await _call(get_async_subagent_service().submit(task=task, sub_agent=sub_agent, runtime=runtime))
    public = {key: item for key, item in value.items() if key not in {"context", "parent_context", "parent_message_id"}}
    return _result(runtime, public, attach=True)


@_registration(RiskLevel.SAFE)
@tool
async def check_async_task(task_id: str, runtime: Runtime) -> Command:
    """Read a background task's live status, result, error, or pending user request.

    Pass the complete task_id. Statuses in message history may be stale.
    A waiting request belongs to the user; never answer or approve on their behalf.
    """
    value = await _call(get_async_subagent_service().get(_parent(runtime), task_id))
    return _result(runtime, value)


@_registration(RiskLevel.SAFE)
@tool
async def list_async_tasks(runtime: Runtime, status: TaskStatus | None = None, limit: int = 50, offset: int = 0) -> Command:
    """List tracked tasks with current statuses, without results. Check a task for its result.

    Use this to recover task IDs after context compaction. Pass IDs in full to other tools.
    """
    if not 1 <= limit <= 100 or offset < 0:
        raise ToolError(ToolErrorCode.INVALID_INPUT, "Limit must be 1-100 and offset must be non-negative.")
    value = await _call(get_async_subagent_service().repository.list_tasks(_parent(runtime),
        status=status, limit=limit, offset=offset))
    return _result(runtime, value)


@_registration(RiskLevel.MUTATING)
@tool
async def update_async_task(task_id: str, instructions: str, runtime: Runtime) -> Command:
    """Queue follow-up instructions on an existing task's thread.

    The current run finishes first. Pending requests for the user remain in place.
    A cancelled task cannot be updated; start a new task instead.
    """
    parent = _parent(runtime)
    messages = (runtime.state or {}).get("messages", [])
    message_id = str(getattr(messages[-1], "id", "") or "") if messages else ""
    key = request_key("update", parent, message_id, str(runtime.tool_call_id))
    value = await _call(get_async_subagent_service().update(parent, task_id, instructions, key))
    return _result(runtime, value)


@_registration(RiskLevel.MUTATING)
@tool
async def cancel_async_task(task_id: str, runtime: Runtime) -> Command:
    """Cancel a background task, its queued instructions, and its descendants."""
    value = await _call(get_async_subagent_service().cancel(_parent(runtime), task_id))
    return _result(runtime, value)
