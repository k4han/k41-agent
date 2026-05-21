from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.langchain.working_dir import get_backend


@tool
def read_file(
    file_path: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Read file content in working directory."""
    try:
        return get_backend(runtime).read_text(file_path)
    except ValueError as e:
        return f"[Error] {str(e)}"
    except FileNotFoundError:
        return f"[Error] File does not exist: {file_path}"
    except Exception as e:
        return f"[Error] {str(e)}"
