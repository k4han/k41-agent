from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.builtin.workspace import get_file_io
from agent.modules.tools.result import ToolError, ToolErrorCode


@register_tool(
    category=ToolCategory.FILE,
    capabilities=[ToolCapability.READ_FS, ToolCapability.REQUIRES_WORKSPACE],
    tags=["fs", "io"],
)
@tool
async def read_file(
    file_path: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Read file content in working directory."""
    try:
        return await (await get_file_io(runtime)).read_text(file_path)
    except FileNotFoundError as exc:
        raise ToolError(
            ToolErrorCode.NOT_FOUND, f"File does not exist: {file_path}"
        ) from exc
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
