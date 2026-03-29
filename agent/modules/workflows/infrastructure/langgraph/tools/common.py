import os
import subprocess
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool


@tool
def read_file(
    file_path: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Read file content in working directory."""
    working_dir = config["configurable"].get("working_dir", ".")
    full_path = os.path.join(working_dir, file_path)
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"[Error] File does not exist: {full_path}"
    except Exception as e:
        return f"[Error] {str(e)}"


@tool
def write_file(
    file_path: str,
    content: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Write content to file in working directory."""
    working_dir = config["configurable"].get("working_dir", ".")
    full_path = os.path.join(working_dir, file_path)
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[OK] Wrote file: {full_path}"
    except Exception as e:
        return f"[Error] {str(e)}"


@tool
def run_bash(
    command: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Run bash command in working directory."""
    working_dir = config["configurable"].get("working_dir", ".")
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout or ""
        error = result.stderr or ""
        return output + (f"\n[stderr]: {error}" if error else "")
    except subprocess.TimeoutExpired:
        return "[Error] Command timed out after 30 seconds"
    except Exception as e:
        return f"[Error] {str(e)}"


@tool
def list_files(
    config: Annotated[RunnableConfig, InjectedToolArg],
    sub_dir: str = "",
) -> str:
    """List files in working directory."""
    working_dir = config["configurable"].get("working_dir", ".")
    target = os.path.join(working_dir, sub_dir) if sub_dir else working_dir
    try:
        files = []
        for root, dirs, filenames in os.walk(target):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in filenames:
                rel = os.path.relpath(os.path.join(root, fname), target)
                files.append(rel)
        return "\n".join(files) if files else "(Empty directory)"
    except Exception as e:
        return f"[Error] {str(e)}"
