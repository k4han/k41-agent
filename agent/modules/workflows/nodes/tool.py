from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from types import SimpleNamespace

from agent.modules.tools import (
    ASK_USER_TOOL_NAME,
    PLAN_MODE_TOOL_NAME,
    ToolResolver,
    get_runtime_context_value,
    get_tool_by_name,
    prepare_tool_batch,
    retain_tool_messages,
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
    import logging

    logger = logging.getLogger(__name__)
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
    # Fall back to "default" like llm_node so bindings and execution agree
    # when the requested agent was deleted or renamed mid-run.
    try:
        from agent.modules.agents import get_catalog_service

        if get_catalog_service().get_agent(agent_name) is None:
            logger.warning("Unknown agent '%s'; falling back to 'default'.", agent_name)
            agent_name = "default"
    except Exception:
        pass

    tools: list[BaseTool] = await ToolResolver().aresolve_for_agent(
        agent_name,
        override_tool_names=allowed_tool_names,
    )
    workspace = get_runtime_context_value(runtime.context, "workspace", None)
    if workspace is None:
        workspace = get_runtime_context_value(runtime.context, "working_dir", None)
    if workspace is not None:
        tools = ToolResolver().for_workspace(tools, workspace)
    tools = _include_pending_control_tools(state, tools)
    calls = getattr(state.get("messages", [])[-1], "tool_calls", []) if state.get("messages") else []
    next_config = {**config, "configurable": dict(config.get("configurable", {}))}
    if calls:
        next_config["configurable"]["coding_message_id"] = str(getattr(state["messages"][-1], "id", "") or "")
        failure = await prepare_tool_batch(tools, calls, SimpleNamespace(
            context=runtime.context, state=state, config=next_config,
        ))
        if failure is not None:
            return await retain_tool_messages({"messages": [ToolMessage(
                content=failure.content, artifact=failure.model_dump(exclude={"content"}),
                name=call["name"], tool_call_id=call["id"], status="error",
            ) for call in calls]}, SimpleNamespace(context=runtime.context, config=next_config))
    result = await ToolNode(tools).ainvoke(state, config=next_config, runtime=runtime)
    from agent.modules.skills import skill_events
    from langgraph.types import Command

    def messages_in(value):
        if isinstance(value, Command):
            return messages_in(value.update)
        if isinstance(value, dict):
            return value.get("messages", [])
        if isinstance(value, list):
            return [message for item in value for message in messages_in(item)]
        return []

    active = skill_events(messages_in(result), state.get("active_skills", {}))
    if active != state.get("active_skills", {}):
        if isinstance(result, dict):
            result["active_skills"] = active
        elif isinstance(result, list):
            result.append(Command(update={"active_skills": active}))
    return await retain_tool_messages(result, SimpleNamespace(context=runtime.context, config=next_config))
