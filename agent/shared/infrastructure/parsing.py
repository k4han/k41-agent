"""Shared parsing utilities."""

from __future__ import annotations

from agent.shared.infrastructure.thinking_parser import (
    extract_thinking_from_text,
    strip_thinking_tags,
)


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text if text.strip() else ""


def _extract_text_from_part(part: object, *, skip_thinking: bool) -> str:
    if isinstance(part, str):
        return _normalize_text(strip_thinking_tags(part))

    if isinstance(part, dict):
        part_type = str(part.get("type", "") or "").strip().lower()
        if skip_thinking and part_type in {"thinking", "reasoning", "reasoning_content"}:
            return ""

        text_value = _normalize_text(part.get("text"))
        if text_value:
            return _normalize_text(strip_thinking_tags(text_value))

        content_value = part.get("content")
        if isinstance(content_value, list):
            return extract_final_text_content(content_value)
        if isinstance(content_value, str):
            return _normalize_text(strip_thinking_tags(content_value))
        return ""

    text_attr = getattr(part, "text", None)
    if isinstance(text_attr, str):
        return _normalize_text(strip_thinking_tags(text_attr))
    return ""


def extract_thinking_content(value: object) -> str:
    """Extract thinking content from model message content.

    Supports both structured ``{"type": "thinking", ...}`` parts and inline
    ``<thinking>...</thinking>`` tags embedded inside string parts.
    """
    from agent.shared.infrastructure.thinking_parser import parse_content_for_thinking

    return parse_content_for_thinking(value).thinking


def extract_final_text_content(value: object) -> str:
    """Extract the final user-visible text from model message content.

    Supports plain strings and structured content blocks (for example Google
    responses that may contain `thinking` + `text` parts). Inline
    ``<thinking>...</thinking>`` style reasoning is stripped from the result
    so end users never see it.
    """
    if isinstance(value, str):
        return _normalize_text(strip_thinking_tags(value))

    if isinstance(value, dict):
        return _extract_text_from_part(value, skip_thinking=False)

    if isinstance(value, list):
        # Prefer the last non-thinking text block.
        for part in reversed(value):
            text = _extract_text_from_part(part, skip_thinking=True)
            if text:
                return text

        # Fallback: return any last extractable text.
        for part in reversed(value):
            text = _extract_text_from_part(part, skip_thinking=False)
            if text:
                return text
        return ""

    return _normalize_text(value)


def extract_tool_display_content(message: object) -> str:
    """Extract UI text while keeping display artifacts out of model content."""
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict) and isinstance(artifact.get("display_content"), str):
        return extract_final_text_content(artifact["display_content"])
    return extract_final_text_content(getattr(message, "content", None))


def parse_string_or_list(value: object, separator: str = ",") -> list[str]:
    """Parse a string (comma-separated) or list into a list of strings.

    Args:
        value: Input value (string, list, or other)
        separator: Separator for string splitting (default: comma)

    Returns:
        List of non-empty trimmed strings
    """
    if isinstance(value, str):
        return [item.strip() for item in value.split(separator) if item.strip()]

    if not isinstance(value, list):
        return []

    items: list[str] = []
    for item in value:
        normalized = str(item).strip()
        if normalized:
            items.append(normalized)
    return items


def safe_str_strip(value: object, default: str = "") -> str:
    """Safely convert value to string and strip whitespace.

    Args:
        value: Input value to convert
        default: Default value if result is empty

    Returns:
        Stripped string or default
    """
    result = str(value or "").strip()
    return result if result else default


__all__ = [
    "extract_final_text_content",
    "extract_tool_display_content",
    "extract_thinking_content",
    "parse_string_or_list",
    "safe_str_strip",
]
