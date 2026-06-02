from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.langchain.working_dir import get_file_io
from agent.modules.tools.result import ToolError, ToolErrorCode


@register_tool(
    category=ToolCategory.FILE,
    capabilities=[ToolCapability.WRITE_FS, ToolCapability.REQUIRES_WORKSPACE],
    tags=["fs", "io"],
)
@tool
async def write_file(
    file_path: str,
    content: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Write content to file in working directory."""
    try:
        return await (await get_file_io(runtime)).write_text(file_path, content)
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
