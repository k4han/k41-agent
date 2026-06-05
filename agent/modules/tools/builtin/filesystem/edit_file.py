from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.builtin.workspace import get_file_io
from agent.modules.tools.result import ToolError, ToolErrorCode


@register_tool(
    category=ToolCategory.FILE,
    capabilities=[ToolCapability.WRITE_FS, ToolCapability.REQUIRES_WORKSPACE],
    tags=["fs", "io", "edit"],
)
@tool
async def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    replace_all: bool = False,
) -> str:
    """Edit a file in working directory by replacing ``old_string`` with ``new_string``.

    By default the replacement is applied to the first occurrence only; the call
    fails when ``old_string`` is missing or matches more than once (to avoid
    ambiguous edits). Set ``replace_all=True`` to substitute every occurrence.
    """
    if not old_string:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "old_string must be a non-empty string.",
        )

    io = await get_file_io(runtime)
    try:
        content = await io.read_text(file_path)
    except FileNotFoundError as exc:
        raise ToolError(
            ToolErrorCode.NOT_FOUND, f"File does not exist: {file_path}"
        ) from exc
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc

    occurrences = content.count(old_string)
    if occurrences == 0:
        raise ToolError(
            ToolErrorCode.NOT_FOUND,
            f"old_string not found in {file_path}.",
        )
    if not replace_all and occurrences > 1:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            (
                f"old_string matches {occurrences} locations in {file_path}; "
                "provide more surrounding context or set replace_all=True."
            ),
        )

    if replace_all:
        updated = content.replace(old_string, new_string)
    else:
        updated = content.replace(old_string, new_string, 1)

    try:
        return await io.write_text(file_path, updated)
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
