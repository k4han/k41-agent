from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.langchain.working_dir import get_backend
from agent.modules.tools.result import ToolError, ToolErrorCode


@register_tool(
    category=ToolCategory.FILE,
    capabilities=[ToolCapability.READ_FS, ToolCapability.REQUIRES_WORKSPACE],
    tags=["fs", "io"],
)
@tool
def read_file(
    file_path: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Read file content in working directory."""
    try:
        return get_backend(runtime).read_text(file_path)
    except FileNotFoundError as exc:
        raise ToolError(
            ToolErrorCode.NOT_FOUND, f"File does not exist: {file_path}"
        ) from exc
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
