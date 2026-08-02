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
    offset: int = 0,
    limit: int = 0,
    line_numbers: bool = False,
) -> str:
    """Read file content in working directory.

    By default the whole file is returned as-is. Use ``offset`` (1-based line)
    and ``limit`` (max number of lines) to page through large files; ``limit``
    of 0 means "until end of file". Set ``line_numbers=True`` to prefix each
    returned line with its 1-based line number. Binary files are reported
    instead of being dumped as text.
    """
    try:
        content = await (await get_file_io(runtime)).read_text(file_path)
    except FileNotFoundError as exc:
        raise ToolError(
            ToolErrorCode.NOT_FOUND, f"File does not exist: {file_path}"
        ) from exc
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc

    if "\0" in content:
        return f"[Binary file detected: {file_path}. Cannot preview as text.]"

    if offset <= 0 and limit <= 0 and not line_numbers:
        return content

    lines = content.splitlines()
    total = len(lines)
    start = max(offset, 1) if offset > 0 else 1
    if start > total:
        if total == 0:
            return f"[File is empty: {file_path} contains no lines.]"
        return f"[No lines in range: offset {offset} exceeds file length ({total} lines).]"
    if limit > 0:
        last = min(start + limit - 1, total)
    else:
        last = total

    selected = lines[start - 1 : last]
    body = "\n".join(selected)
    if line_numbers:
        width = len(str(last))
        numbered = [
            f"{start + index:>{width}}: {line}"
            for index, line in enumerate(selected)
        ]
        body = "\n".join(numbered)
    return f"[lines {start}-{last} of {total}]\n{body}"
