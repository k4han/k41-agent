import subprocess
from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.langchain.working_dir import get_backend


@tool
def run_bash(
    command: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Run bash command in working directory."""
    try:
        return get_backend(runtime).execute(command, timeout=30).output
    except ValueError as e:
        return f"[Error] {str(e)}"
    except subprocess.TimeoutExpired:
        return "[Error] Command timed out after 30 seconds"
    except Exception as e:
        return f"[Error] {str(e)}"
