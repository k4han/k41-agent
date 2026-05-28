from langchain_core.tools import tool

from agent.modules.skills import get_skill_content_xml
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode


@register_tool(category=ToolCategory.SKILL, tags=["skill"])
@tool
def skill(name: str) -> str:
    """
    LLM có thể gọi tool này để sử dụng các skill đã được đăng ký trong hệ thống.
    """
    content_xml = get_skill_content_xml(name)
    if content_xml is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, "skill not found")
    return content_xml