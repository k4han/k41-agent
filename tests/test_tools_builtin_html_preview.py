"""Tests for the built-in ``html_preview`` tool."""

from agent.modules.tools.builtin.utility.html_preview import html_preview
from agent.modules.tools.decorators import META_ATTR
from agent.modules.tools.domain import ToolCategory


def test_html_preview_returns_short_confirmation() -> None:
    result = html_preview.invoke({"html": "<p>hello</p>", "title": "Demo"})

    assert result == f"HTML preview ready ({len('<p>hello</p>')} chars): Demo"


def test_html_preview_registration_metadata() -> None:
    meta = getattr(html_preview, META_ATTR)

    assert meta is not None
    assert meta.category is ToolCategory.UTILITY
    assert "html" in meta.tags


def test_html_preview_name() -> None:
    assert html_preview.name == "html_preview"


def test_html_preview_with_mode() -> None:
    result = html_preview.invoke(
        {"html": "<div>card</div>", "title": "Card Demo", "mode": "card"}
    )
    assert result == f"HTML preview ready ({len('<div>card</div>')} chars): Card Demo"

    result_page = html_preview.invoke(
        {"html": "<html>...</html>", "title": "Page Demo", "mode": "full_page"}
    )
    assert result_page == f"HTML preview ready ({len('<html>...</html>')} chars): Page Demo"
