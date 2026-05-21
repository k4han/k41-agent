from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.langchain.working_dir import get_backend


@tool
def write_file(
    file_path: str,
    content: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Write content to file in working directory."""
    try:
        return get_backend(runtime).write_text(file_path, content)
    except ValueError as e:
        return f"[Error] {str(e)}"
    except Exception as e:
        return f"[Error] {str(e)}"
