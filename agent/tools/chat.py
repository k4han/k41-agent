# agent/tools/chat.py
# Tools dành riêng cho chat agent

from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """Trả về thời gian hiện tại."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def echo(text: str) -> str:
    """Echo lại text (dùng để test)."""
    return f"Echo: {text}"
