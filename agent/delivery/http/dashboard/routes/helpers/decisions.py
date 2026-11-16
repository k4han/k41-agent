"""Configured model choices and validation for decision routing settings."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.providers import (
    SUPPORTED_PROVIDER_TYPES,
    build_provider_rows_with_sessions,
    normalize_provider_name,
)
from agent.shared.config import ConfigService, PROVIDER_SETTING_FIELD_ORDER
from agent.shared.config.decision_providers import is_configured, provider_entries
from agent.shared.infrastructure.parsing import parse_string_or_list

DECISION_SETTING_KEYS = frozenset({
    "decision.default_provider", "decision.router.llm_model", "decision.router.mode",
    "decision.router.threshold", "decision.router.log_telemetry",
})


def validated_default_decision_provider(service: ConfigService, value: str | None) -> str:
    """Resolve a configured Decision connection using its saved name."""
    if value is not None and not isinstance(value, str):
        raise HTTPException(400, "Decision provider name must be a string.")
    name = (value or "").strip()
    if not name:
        return ""
    entry = provider_entries(service.get_all()).get(name.lower())
    if entry is None:
        raise HTTPException(404, "Decision provider not found.")
    if not is_configured(entry):
        raise HTTPException(400, "Decision provider configuration is incomplete.")
    return entry["name"]


async def configured_router_llm_options(
    request: Request, updates: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """List only saved models, without catalog expansion or remote discovery."""
    flat = {**get_request_config_service(request).get_all(), **(updates or {})}
    rows = await build_provider_rows_with_sessions(
        request,
        {key: {"value": value} for key, value in flat.items() if key.startswith("llm.providers.")},
        PROVIDER_SETTING_FIELD_ORDER,
        "",
    )
    options = []
    for row in rows:
        if not row["ready"] or row["type"] not in SUPPORTED_PROVIDER_TYPES:
            continue
        default_model = str(row["fields"]["default_model"]["info"]["value"] or "").strip()
        models = row["fields"]["models"]["info"]["value"]
        if isinstance(models, str):
            models = models.replace("\n", ",")
        for model in dict.fromkeys([default_model, *parse_string_or_list(models)]):
            if model:
                options.append({"value": f"{row['name']}/{model}", "label": f"{row['name']} / {model}"})
    return options


async def validate_router_model_update(values: dict[str, Any], *, request: Request) -> None:
    """Validate and canonicalize a changed router model against prospective settings."""
    key = "decision.router.llm_model"
    if not values.get(key):
        return
    provider, separator, model = values[key].partition("/")
    if separator and model.strip():
        for option in await configured_router_llm_options(request, values):
            saved_provider, saved_model = option["value"].split("/", 1)
            if normalize_provider_name(provider) == normalize_provider_name(saved_provider) and model.strip() == saved_model:
                values[key] = option["value"]
                return
    raise HTTPException(400, "Select a configured model from an enabled, ready provider.")
