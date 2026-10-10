from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory
from agent.modules.tools.middleware import apply_default_middleware
from agent.shared.timezone import resolve_display_timezone


@register_tool(category=ToolCategory.UTILITY, tags=["time"])
@tool
def get_current_time() -> str:
    """Return current time with configured timezone."""
    tz_name, zone = resolve_display_timezone()
    now = datetime.now(zone)
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} ({tz_name})"


apply_default_middleware(get_current_time)

