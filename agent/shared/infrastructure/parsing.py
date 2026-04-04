"""Shared parsing utilities."""

from __future__ import annotations


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


__all__ = ["parse_string_or_list", "safe_str_strip"]
