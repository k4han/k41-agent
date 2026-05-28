from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from agent.modules.mcp import get_all_mcp_tools, get_mcp_server_tools
from agent.modules.tools import (
    get_default_tools,
    get_runtime_context_value,
    resolve_tools,
)
from agent.modules.workflows.run_config import WorkflowContext


def make_tool_node(tools: list[BaseTool]) -> ToolNode:
    """Create a ToolNode from a list of tools."""
    return ToolNode(tools)


async def _collect_mcp_tools(
    allowed_tool_names: list[str] | tuple[str, ...] | None,
    agent_name: str = "default",
    mcp_servers: list[str] | None = None,
) -> list[BaseTool]:
    """Return MCP tools relevant for this Tool call.

    - If ``mcp_servers`` is provided (ticked), load only tools for those servers.
    - ``allowed_tool_names is None`` means the agent uses the full default toolset, so
      include every loaded MCP tool.
    - If ``allowed_tool_names`` does not contain any tool starting with 'mcp__' and the
      agent is the 'default' chat agent, we also include every loaded MCP tool
      to make dynamically installed MCP tools instantly available.
    - Otherwise only include MCP tools whose prefixed name was explicitly listed.
    """
    if mcp_servers is not None:
        tools = []
        for server in mcp_servers:
            try:
                server_tools = await get_mcp_server_tools(server)
                tools.extend(server_tools)
            except Exception:
                pass
        return tools

    try:
        all_mcp = await get_all_mcp_tools()
    except Exception:
        return []
    if not all_mcp:
        return []
    if allowed_tool_names is None:
        return list(all_mcp)
    wanted = {name for name in allowed_tool_names if name.startswith("mcp__")}
    if not wanted:
        if agent_name == "default":
            return list(all_mcp)
        return []
    return [tool for tool in all_mcp if tool.name in wanted]


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
    base_tools: list[BaseTool] = list(
        get_default_tools()
        if allowed_tool_names is None
        else resolve_tools(allowed_tool_names)
    )

    # Load agent config from catalog to get ticked mcp_servers
    from agent.modules.agents import get_catalog_service
    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name) if agent_name else None
    if agent_config is None:
        agent_config = catalog.get_agent("default")

    mcp_servers = agent_config.mcp_servers if agent_config else None

    mcp_tools = await _collect_mcp_tools(
        allowed_tool_names,
        agent_name=agent_name,
        mcp_servers=mcp_servers,
    )
    if mcp_tools:
        known_names = {tool.name for tool in base_tools}
        base_tools.extend(tool for tool in mcp_tools if tool.name not in known_names)
    return await ToolNode(base_tools).ainvoke(state, config=config)
