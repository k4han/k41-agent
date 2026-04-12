from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from agent.modules.tools.public import (
    get_default_tools,
    get_runtime_context_value,
    resolve_tools,
)
from agent.modules.workflows.infrastructure.langgraph.run_config import WorkflowContext


def make_tool_node(tools: list[BaseTool]) -> ToolNode:
    """
    Tạo ToolNode từ danh sách tools.
    ToolNode của LangGraph tự handle việc gọi tool và trả kết quả.
    Config (working_dir, ...) được truyền qua RunnableConfig/InjectedToolArg.
    """
    return ToolNode(tools)


async def tool_node(
    state,
    config: RunnableConfig,
    runtime: Runtime[WorkflowContext],
):
    """Resolve the executable tool set at runtime to match llm_node bindings."""
    allowed_tool_names = get_runtime_context_value(
        runtime.context,
        "allowed_tool_names",
        None,
    )
    tools = (
        get_default_tools()
        if allowed_tool_names is None
        else resolve_tools(allowed_tool_names)
    )
    return await ToolNode(tools).ainvoke(state, config=config)
