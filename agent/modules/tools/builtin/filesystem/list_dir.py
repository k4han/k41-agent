from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.builtin.workspace import get_file_io
from agent.modules.tools.result import ToolError, ToolErrorCode


@tool
async def list_dir(
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    path: str = "",
) -> str:
    """List files and folders in working directory."""
    try:
        return await (await get_file_io(runtime)).list_dir(path)
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc


# Keep direct internal callers compatible without catalog registration.
from agent.modules.tools.middleware import apply_default_middleware

apply_default_middleware(list_dir)
