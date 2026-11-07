"""Estimate session costs from recorded tokens and configured catalog prices."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.modules.providers import ProviderConfig


def _price(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def estimate_usage_cost(
    models: list[dict[str, Any]],
    provider_lookup: Callable[[str], ProviderConfig],
) -> dict[str, Any]:
    """Return a USD estimate, marking unknown prices or token reports incomplete."""
    from agent.modules.providers import ProviderType, get_provider_catalog_entry

    total = 0.0
    priced = False
    complete = bool(models)
    for usage in models:
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        if usage.get("missing_usage_count", 0):
            complete = False
        if not input_tokens and not output_tokens and usage.get("missing_usage_count", 0):
            continue
        try:
            provider = provider_lookup(usage["provider"])
            # Account subscriptions do not have an on-demand token bill.
            entry = None if provider.provider_type == ProviderType.CHATGPT else get_provider_catalog_entry(
                provider.catalog_id or provider.name,
            )
            model = next((model for model in entry.models if model.id == usage["model"]), None) if entry else None
        except (KeyError, ValueError, RuntimeError):
            model = None
        input_price = _price(model.cost_input) if model else None
        output_price = _price(model.cost_output) if model else None
        if model is None or (input_tokens and input_price is None) or (output_tokens and output_price is None):
            complete = False
            continue
        total += (input_tokens * (input_price or 0) + output_tokens * (output_price or 0)) / 1_000_000
        priced = True
    return {"estimated_cost_usd": round(total, 8) if priced else None, "cost_complete": complete}
