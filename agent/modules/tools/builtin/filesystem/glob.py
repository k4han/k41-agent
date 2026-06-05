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
    tags=["fs", "search"],
)
@tool
async def glob(
    pattern: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    path: str = "",
    include_dirs: bool = False,
) -> str:
    """Find files in the workspace by glob pattern.

    Patterns follow standard glob syntax (``*``, ``?``, ``**``). Results are
    returned as relative paths, one per line. Ignored directories such as
    ``.git`` and ``node_modules`` are excluded. Set ``include_dirs=True`` to
    also match directories.
    """
    if not pattern or not pattern.strip():
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "Pattern must be a non-empty string.",
        )
    try:
        return await (await get_file_io(runtime)).glob(
            pattern,
            path=path,
            include_dirs=include_dirs,
        )
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
