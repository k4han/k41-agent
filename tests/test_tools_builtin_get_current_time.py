"""Tests for the built-in ``get_current_time`` tool."""

from __future__ import annotations

import re
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent.modules.tools.builtin.utility.get_current_time import get_current_time
from agent.modules.tools.decorators import META_ATTR
from agent.modules.tools.domain import ToolCategory


def test_get_current_time_registration_metadata() -> None:
    meta = getattr(get_current_time, META_ATTR)

    assert meta is not None
    assert meta.category is ToolCategory.UTILITY
    assert "time" in meta.tags
    assert get_current_time.name == "get_current_time"


def test_get_current_time_default_display_timezone() -> None:
    result = get_current_time.invoke({})
    # Matches format YYYY-MM-DD HH:MM:SS (<configured_timezone>)
    pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(.+\)$"
    assert re.match(pattern, result)


def test_get_current_time_uses_configured_display_timezone() -> None:
    with patch(
        "agent.modules.tools.builtin.utility.get_current_time.resolve_display_timezone",
        return_value=("Asia/Bangkok", ZoneInfo("Asia/Bangkok")),
    ):
        result = get_current_time.invoke({})

    assert result.endswith("(Asia/Bangkok)")
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(Asia/Bangkok\)$", result)


def test_get_current_time_with_ho_chi_minh_timezone() -> None:
    with patch(
        "agent.modules.tools.builtin.utility.get_current_time.resolve_display_timezone",
        return_value=("Asia/Ho_Chi_Minh", ZoneInfo("Asia/Ho_Chi_Minh")),
    ):
        result = get_current_time.invoke({})

    assert result.endswith("(Asia/Ho_Chi_Minh)")
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(Asia/Ho_Chi_Minh\)$", result)
