from langchain_core.tools import tool


@tool
def echo(text: str) -> str:
    """Echo back the text (used for testing)."""
    return f"Echo: {text}"
