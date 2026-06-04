from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from agent.modules.tools import (
    PLAN_MODE_TOOL_NAME,
    ToolResolver,
    get_runtime_context_value,
    get_tool_by_name,
)
from agent.modules.workflows.run_config import WorkflowContext


def make_tool_node(tools: list[BaseTool]) -> ToolNode:
    """Create a ToolNode from a list of tools."""
    return ToolNode(tools)


def _parallel_write_todos_error(state) -> dict | None:
    messages = state.get("messages", []) if isinstance(state, dict) else []
    if not messages:
        return None

    tool_calls = getattr(messages[-1], "tool_calls", None) or []
    write_todos_calls = [
        call
        for call in tool_calls
        if isinstance(call, dict) and call.get("name") == "write_todos"
    ]
    if len(write_todos_calls) <= 1:
        return None

    return {
        "messages": [
            ToolMessage(
                content=(
                    "Error: The write_todos tool should not be called multiple "
                    "times in parallel. Call it once per model turn with the "
                    "complete updated todo list."
                ),
                tool_call_id=str(call.get("id", "")),
                status="error",
            )
            for call in write_todos_calls
        ]
    }


def _last_tool_call_names(state) -> set[str]:
    messages = state.get("messages", []) if isinstance(state, dict) else []
    if not messages:
        return set()
    tool_calls = getattr(messages[-1], "tool_calls", None) or []
    return {
        str(call.get("name") or "")
        for call in tool_calls
        if isinstance(call, dict) and call.get("name")
    }


def _include_pending_control_tools(state, tools: list[BaseTool]) -> list[BaseTool]:
    """Allow resumed control tools to finish even after switching agents."""
    pending_names = _last_tool_call_names(state)
    if PLAN_MODE_TOOL_NAME not in pending_names:
        return tools
    if any(getattr(tool, "name", "") == PLAN_MODE_TOOL_NAME for tool in tools):
        return tools

    plan_tool = get_tool_by_name(PLAN_MODE_TOOL_NAME)
    if plan_tool is None:
        return tools
    return [*tools, plan_tool]


async def tool_node(
    state,
    config: RunnableConfig,
    runtime: Runtime[WorkflowContext],
):
    """Resolve the executable tool set at runtime to match llm_node bindings."""
    todo_error = _parallel_write_todos_error(state)
    if todo_error is not None:
        return todo_error

    allowed_tool_names = get_runtime_context_value(
        runtime.context,
        "allowed_tool_names",
        None,
    )
    agent_name = get_runtime_context_value(
        runtime.context,
        "agent_name",
        "default",
    )

    tools: list[BaseTool] = await ToolResolver().aresolve_for_agent(
        agent_name,
        override_tool_names=allowed_tool_names,
    )
    tools = _include_pending_control_tools(state, tools)
    return await ToolNode(tools).ainvoke(state, config=config)
