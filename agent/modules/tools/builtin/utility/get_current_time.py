from langchain_core.tools import tool

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory


@register_tool(category=ToolCategory.UTILITY, tags=["time"])
@tool
def get_current_time() -> str:
    """Return current time with timezone."""
    from agent.shared.timezone import display_now

    now = display_now()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")
