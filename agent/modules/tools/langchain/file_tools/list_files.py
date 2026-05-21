from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.langchain.working_dir import get_backend


@tool
def list_files(
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    sub_dir: str = "",
) -> str:
    """List files in working directory."""
    try:
        return get_backend(runtime).list_files(sub_dir)
    except ValueError as e:
        return f"[Error] {str(e)}"
    except Exception as e:
        return f"[Error] {str(e)}"
