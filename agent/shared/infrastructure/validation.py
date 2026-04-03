"""Common validation utilities."""


def is_placeholder_value(value: str) -> bool:
    """Check if config value is empty or a placeholder.

    Returns True if the value is:
    - Empty string
    - Starts with "your-" (common placeholder pattern)
    """
    return not value or value.startswith("your-")
