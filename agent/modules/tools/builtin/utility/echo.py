from langchain_core.tools import tool

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory


@register_tool(category=ToolCategory.UTILITY, tags=["debug"])
@tool
def echo(text: str) -> str:
    """Echo back the text (used for testing)."""
    return f"Echo: {text}"