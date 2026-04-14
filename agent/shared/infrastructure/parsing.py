"""Shared parsing utilities."""

from __future__ import annotations


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text if text.strip() else ""


def _extract_text_from_part(part: object, *, skip_thinking: bool) -> str:
    if isinstance(part, str):
        return _normalize_text(part)

    if isinstance(part, dict):
        part_type = str(part.get("type", "") or "").strip().lower()
        if skip_thinking and part_type == "thinking":
            return ""

        text_value = _normalize_text(part.get("text"))
        if text_value:
            return text_value

        content_value = part.get("content")
        if isinstance(content_value, list):
            return extract_final_text_content(content_value)
        if isinstance(content_value, str):
            return _normalize_text(content_value)
        return ""

    text_attr = getattr(part, "text", None)
    return _normalize_text(text_attr)


def extract_final_text_content(value: object) -> str:
    """Extract the final user-visible text from model message content.

    Supports plain strings and structured content blocks (for example Google
    responses that may contain `thinking` + `text` parts).
    """
    if isinstance(value, str):
        return _normalize_text(value)

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
    "parse_string_or_list",
    "safe_str_strip",
]
