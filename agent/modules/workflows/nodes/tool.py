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
    workspace = get_runtime_context_value(runtime.context, "workspace", None)
    if workspace is None:
        workspace = get_runtime_context_value(runtime.context, "working_dir", None)
    if workspace is not None:
        tools = ToolResolver().for_workspace(tools, workspace, agent_name)
    tools = _include_pending_control_tools(state, tools)
    # Authorize the whole coding batch before any tool can mutate files or
    # start a process. Completed settlements survive node replay.
    from types import SimpleNamespace
    from agent.modules.tools import invocation_context, CodingError, get_coding_service
    from pydantic import ValidationError

    calls = getattr(state.get("messages", [])[-1], "tool_calls", []) if state.get("messages") else []
    by_name = {tool.name: tool for tool in tools}
    next_config = {**config, "configurable": dict(config.get("configurable", {}))}
    if calls and any(getattr(tool, "coding_definition", None) for tool in tools):
        next_config["configurable"]["coding_message_id"] = str(getattr(state["messages"][-1], "id", "") or "")
    for call in calls:
        definition = getattr(by_name.get(call["name"]), "coding_definition", None)
        if definition is None:
            continue
        try:
            context = invocation_context(SimpleNamespace(context=runtime.context, state=state, config=next_config, tool_call_id=call["id"]))
            service = get_coding_service()
            if not service.storage.journal_path(context).exists():
                parsed = definition.input_schema.model_validate(call["args"])
                await service.prepare(definition.name, parsed.model_dump(), context)
        except (CodingError, ValidationError, OSError, ValueError) as exc:
            result = get_coding_service().failure(exc)
            result.data["batch_stopped_before_execution"] = True
            result.content = f"[error] Batch stopped before execution: {exc}"
            from agent.modules.tools import retain_tool_messages
            return await retain_tool_messages(
                {"messages": [ToolMessage(content=result.content, artifact=result.model_dump(exclude={"content"}),
                     name=item["name"], tool_call_id=item["id"], status="error") for item in calls]},
                SimpleNamespace(context=runtime.context, config=next_config),
            )
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
    from agent.modules.tools import retain_tool_messages
    return await retain_tool_messages(result, SimpleNamespace(context=runtime.context, config=next_config))
