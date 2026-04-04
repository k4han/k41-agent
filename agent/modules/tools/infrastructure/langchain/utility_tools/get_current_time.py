from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """Return current time."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
