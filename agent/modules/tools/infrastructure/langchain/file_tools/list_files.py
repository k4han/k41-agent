import os

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.infrastructure.langchain.working_dir import get_working_dir
from agent.modules.tools.infrastructure.runtime.path_guard import resolve_safe_path


@tool
def list_files(
    runtime: ToolRuntime,
    sub_dir: str = "",
) -> str:
    """List files in working directory."""
    working_dir = get_working_dir(runtime)
    try:
        target = resolve_safe_path(working_dir, sub_dir or ".")
        files = []
        for root, dirs, filenames in os.walk(target):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in filenames:
                rel = os.path.relpath(os.path.join(root, fname), target)
                files.append(rel)
        return "\n".join(files) if files else "(Empty directory)"
    except ValueError as e:
        return f"[Error] {str(e)}"
    except Exception as e:
        return f"[Error] {str(e)}"
