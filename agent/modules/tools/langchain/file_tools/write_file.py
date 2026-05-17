import os
from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.langchain.working_dir import get_working_dir
from agent.modules.tools.runtime.path_guard import resolve_safe_path


@tool
def write_file(
    file_path: str,
    content: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Write content to file in working directory."""
    working_dir = get_working_dir(runtime)
    try:
        full_path = resolve_safe_path(working_dir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[OK] Wrote file: {full_path}"
    except ValueError as e:
        return f"[Error] {str(e)}"
    except Exception as e:
        return f"[Error] {str(e)}"
