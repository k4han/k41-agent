from typing import Literal

from langchain_core.tools import tool

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory

HtmlPreviewMode = Literal["auto", "card", "full_page"]


@register_tool(category=ToolCategory.UTILITY, tags=["html", "preview"])
@tool
def html_preview(html: str, title: str = "HTML Preview", mode: HtmlPreviewMode = "auto") -> str:
    """Render an interactive HTML preview in the chat transcript.

    The chat preview renders inside a sandboxed iframe with device viewport controls
    (Responsive, Desktop, Tablet, Mobile), zoom/scale-to-fit, and an 'Open in new tab' option.

    Guidelines for best visual results:
    1. Visual Cards & Widgets (Default & Recommended):
       - Present structured data visually: KPI metric cards, comparison tables,
         timeline milestones, progress bars, SVG/CSS charts, or interactive widgets.
       - Return a self-contained HTML fragment (e.g. <div style="...">...</div> with <style>).
       - Do NOT include <html>, <head>, <body>, or full-site navbar/footer headers.
       - Use clean inline styles or a scoped <style> block with modern aesthetics:
         clean borders, rounded corners (border-radius: 8px to 12px), subtle shadows,
         and accessible contrast that looks great on both light and dark backgrounds.
       - Set mode="card" or mode="auto".

    2. Full Webpages & Application Mockups:
       - If the user explicitly asks for a complete website, landing page, or web app,
         provide the full HTML document and set mode="full_page".
       - Include <meta name="viewport" content="width=device-width, initial-scale=1">
         and responsive CSS rules.
       - The UI will automatically provide Desktop scale-to-fit preview and an
         'Open in new tab' option for full-page views.

    Args:
        html: Self-contained HTML fragment (card/widget) or full HTML document.
        title: Short title displayed above the preview.
        mode: Display hint: "auto" (auto-detected), "card" (compact widget),
            or "full_page" (complete web page mockup).
    """
    return f"HTML preview ready ({len(html)} chars): {title}"