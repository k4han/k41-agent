from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime
from typing import Annotated

from agent.modules.tools.infrastructure.langchain.working_dir import get_working_dir
from agent.modules.tools.infrastructure.runtime.path_guard import resolve_safe_path


@tool
def read_file(
    file_path: str,
    runtime: Annotated[ToolRuntime, InjectedToolArg],
) -> str:
    """Read file content in working directory."""
    working_dir = get_working_dir(runtime)
    try:
        full_path = resolve_safe_path(working_dir, file_path)
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    except ValueError as e:
        return f"[Error] {str(e)}"
    except FileNotFoundError:
        return f"[Error] File does not exist: {full_path}"
    except Exception as e:
        return f"[Error] {str(e)}"
