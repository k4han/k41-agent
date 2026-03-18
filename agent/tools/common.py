# agent/tools/common.py
# Tools dùng chung, nhận working_dir qua InjectedToolArg từ config

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
    """Đọc nội dung file trong working directory."""
    working_dir = config["configurable"].get("working_dir", ".")
    full_path = os.path.join(working_dir, file_path)
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"[Error] File không tồn tại: {full_path}"
    except Exception as e:
        return f"[Error] {str(e)}"


@tool
def write_file(
    file_path: str,
    content: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Ghi nội dung vào file trong working directory."""
    working_dir = config["configurable"].get("working_dir", ".")
    full_path = os.path.join(working_dir, file_path)
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[OK] Đã ghi file: {full_path}"
    except Exception as e:
        return f"[Error] {str(e)}"


@tool
def run_bash(
    command: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """Chạy bash command trong working directory."""
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
        error  = result.stderr or ""
        return output + (f"\n[stderr]: {error}" if error else "")
    except subprocess.TimeoutExpired:
        return "[Error] Command timeout sau 30 giây"
    except Exception as e:
        return f"[Error] {str(e)}"


@tool
def list_files(
    config: Annotated[RunnableConfig, InjectedToolArg],
    sub_dir: str = "",
) -> str:
    """Liệt kê files trong working directory."""
    working_dir = config["configurable"].get("working_dir", ".")
    target = os.path.join(working_dir, sub_dir) if sub_dir else working_dir
    try:
        files = []
        for root, dirs, filenames in os.walk(target):
            # Bỏ qua hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in filenames:
                rel = os.path.relpath(os.path.join(root, fname), target)
                files.append(rel)
        return "\n".join(files) if files else "(Thư mục rỗng)"
    except Exception as e:
        return f"[Error] {str(e)}"
