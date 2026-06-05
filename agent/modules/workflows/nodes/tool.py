from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from agent.modules.tools import (
    ASK_USER_TOOL_NAME,
    PLAN_MODE_TOOL_NAME,
    ToolResolver,
    get_runtime_context_value,
    get_tool_by_name,
)
from agent.modules.workflows.run_config import WorkflowContext


def make_tool_node(tools: list[BaseTool]) -> ToolNode:
    """Create a ToolNode from a list of tools."""
    return ToolNode(tools)


def _parallel_named_tool_error(state, tool_name: str, message: str) -> dict | None:
    messages = state.get("messages", []) if isinstance(state, dict) else []
    if not messages:
        return None

    tool_calls = getattr(messages[-1], "tool_calls", None) or []
    matching_calls = [
        call
        for call in tool_calls
        if isinstance(call, dict) and call.get("name") == tool_name
    ]
    if len(matching_calls) <= 1:
        return None

    return {
        "messages": [
            ToolMessage(
                content=message,
                tool_call_id=str(call.get("id", "")),
                status="error",
            )
            for call in matching_calls
        ]
    }


def _parallel_control_tool_error(state) -> dict | None:
    return _parallel_named_tool_error(
        state,
        "write_todos",
        (
            "Error: The write_todos tool should not be called multiple "
            "times in parallel. Call it once per model turn with the "
            "complete updated todo list."
        ),
    ) or _parallel_named_tool_error(
        state,
        ASK_USER_TOOL_NAME,
        (
            "Error: The ask_user tool should not be called multiple times "
            "in parallel. Ask all pending user questions in one ask_user call."
        ),
    )


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
    control_names = {PLAN_MODE_TOOL_NAME, ASK_USER_TOOL_NAME}
    pending_control_names = pending_names.intersection(control_names)
    if not pending_control_names:
        return tools
    existing_names = {getattr(tool, "name", "") for tool in tools}
    next_tools = list(tools)
    for name in sorted(pending_control_names):
        if name in existing_names:
            continue
        pending_tool = get_tool_by_name(name)
        if pending_tool is not None:
            next_tools.append(pending_tool)
    return next_tools


async def tool_node(
    state,
    config: RunnableConfig,
    runtime: Runtime[WorkflowContext],
):
    """Resolve the executable tool set at runtime to match llm_node bindings."""
    control_error = _parallel_control_tool_error(state)
    if control_error is not None:
        return control_error

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
