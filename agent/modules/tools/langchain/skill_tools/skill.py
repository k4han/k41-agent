from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from agent.modules.skills import get_effective_skill_content_xml
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.context import get_context_value, get_thread_id


@register_tool(category=ToolCategory.SKILL, tags=["skill"])
@tool
async def skill(
    name: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """
    Load instructions for an available skill by name.
    """
    allowed_names = get_context_value(runtime.context, "allowed_skill_names", None)
    content_xml = await get_effective_skill_content_xml(
        name,
        allowed_names=allowed_names,
        workspace=get_context_value(runtime.context, "workspace", None),
        thread_id=get_thread_id(getattr(runtime, "config", None)),
    )
    if content_xml is None:
        allowed = (
            None
            if allowed_names is None
            else {str(value).strip() for value in allowed_names if str(value).strip()}
        )
        if allowed is not None and str(name or "").strip() not in allowed:
            raise ToolError(ToolErrorCode.PERMISSION_DENIED, "skill is not available")
        raise ToolError(ToolErrorCode.NOT_FOUND, "skill not found")
    return content_xml
