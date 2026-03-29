from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode


def make_tool_node(tools: list[BaseTool]) -> ToolNode:
    """
    Tạo ToolNode từ danh sách tools.
    ToolNode của LangGraph tự handle việc gọi tool và trả kết quả.
    Config (working_dir, ...) được truyền qua RunnableConfig/InjectedToolArg.
    """
    return ToolNode(tools)
