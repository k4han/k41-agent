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
async def grep(
    pattern: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    path: str = "",
    include: str | None = None,
    case_insensitive: bool = False,
    max_results: int = 100,
) -> str:
    """Search for ``pattern`` in workspace files.

    ``pattern`` is treated as a regular expression (matching is unanchored
    and case-sensitive by default). Use ``include`` to restrict the search
    to files whose name matches a glob (e.g. ``*.py``). Results are
    formatted as ``path:line: matched text`` and are truncated to
    ``max_results`` entries (capped at 100).
    """
    if not pattern or not pattern.strip():
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "Pattern must be a non-empty string.",
        )
    try:
        return await (await get_file_io(runtime)).grep(
            pattern,
            path=path,
            include=include,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
