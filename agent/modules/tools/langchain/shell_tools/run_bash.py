import subprocess
from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.langchain.working_dir import get_backend
from agent.modules.tools.result import ToolError, ToolErrorCode


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
        ToolCapability.REQUIRES_WORKSPACE,
    ],
    tags=["shell"],
)
@tool
def run_bash(
    command: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Run bash command in working directory."""
    try:
        return get_backend(runtime).execute(command, timeout=30).output
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Command timed out after 30 seconds"
        ) from exc
