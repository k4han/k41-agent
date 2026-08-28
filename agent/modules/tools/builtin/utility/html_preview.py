from langchain_core.tools import tool

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory


@register_tool(category=ToolCategory.UTILITY, tags=["html", "preview"])
@tool
def html_preview(html: str, title: str = "HTML Preview") -> str:
    """Show an HTML preview in the dashboard UI.

    The provided HTML is rendered in a sandboxed frame so the user can see
    the result visually. Use this whenever you build or modify an HTML
    document and want the user to review it.

    Args:
        html: Full HTML document or HTML fragment to preview.
        title: Short title displayed above the preview.
    """
    return f"HTML preview ready ({len(html)} chars): {title}"