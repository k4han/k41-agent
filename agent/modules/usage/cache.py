"""Read cache hits from LangChain's normalized input token details."""

from typing import Any


def cache_read_tokens(details: Any, input_tokens: int | None) -> int | None:
    """Sum cache-read partitions without counting cache writes or missing data."""
    if not isinstance(details, dict) or type(input_tokens) is not int or input_tokens < 0:
        return None
    values = [details[key] for key in ("cache_read", "priority_cache_read", "flex_cache_read")
              if details.get(key) is not None]
    if not values or any(type(value) is not int or value < 0 for value in values):
        return None
    total = sum(values)
    return total if total <= input_tokens else None
