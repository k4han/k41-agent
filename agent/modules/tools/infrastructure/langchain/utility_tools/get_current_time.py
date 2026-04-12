from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """Return current time with timezone."""
    from datetime import datetime

    now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")
