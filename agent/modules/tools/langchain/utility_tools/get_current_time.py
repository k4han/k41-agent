from langchain_core.tools import tool

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory


@register_tool(category=ToolCategory.UTILITY, tags=["time"])
@tool
def get_current_time() -> str:
    """Return current time with timezone."""
    from datetime import datetime

    now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")