# agent/tools/chat.py
# Tools specific to chat agent

from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """Return current time."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def echo(text: str) -> str:
    """Echo back the text (used for testing)."""
    return f"Echo: {text}"
