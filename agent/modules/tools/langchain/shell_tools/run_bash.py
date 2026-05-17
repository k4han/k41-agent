import subprocess
from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.langchain.working_dir import get_working_dir
from agent.modules.tools.runtime.path_guard import resolve_safe_path


@tool
def run_bash(
    command: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Run bash command in working directory."""
    working_dir = get_working_dir(runtime)
    try:
        safe_working_dir = resolve_safe_path(working_dir, ".")
        result = subprocess.run(
            command,
            shell=True,
            cwd=safe_working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout or ""
        error = result.stderr or ""
        return output + (f"\n[stderr]: {error}" if error else "")
    except ValueError as e:
        return f"[Error] {str(e)}"
    except subprocess.TimeoutExpired:
        return "[Error] Command timed out after 30 seconds"
    except Exception as e:
        return f"[Error] {str(e)}"
