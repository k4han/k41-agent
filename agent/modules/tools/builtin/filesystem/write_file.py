from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.skills import invalidate_repository_skills_for_path
from agent.modules.tools.builtin.workspace import get_file_io
from agent.modules.tools.result import ToolError, ToolErrorCode


@tool
async def write_file(
    file_path: str,
    content: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    append: bool = False,
) -> str:
    """Write content to file in working directory.

    By default the file is replaced with ``content`` (atomic write). Set
    ``append=True`` to add ``content`` to the end of the file instead.
    """
    try:
        result = await (await get_file_io(runtime)).write_text(
            file_path, content, append=append
        )
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc

    invalidate_repository_skills_for_path(file_path)
    return result


# Keep direct internal callers compatible without catalog registration.
from agent.modules.tools.middleware import apply_default_middleware

apply_default_middleware(write_file)
