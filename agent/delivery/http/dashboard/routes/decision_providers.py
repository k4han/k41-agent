"""Dashboard management of named decision model providers."""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.settings import delete_config_tree, update_config_settings
from agent.shared.config.decision_providers import (
    DECISION_PROVIDERS, is_configured, provider_entries, provider_payload, validate_provider_name,
)

router = APIRouter()


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str
    fields: dict[str, Any] = Field(default_factory=dict)


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, Any]


class DefaultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None


def validated_fields(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    definition = DECISION_PROVIDERS.get(kind)
    if definition is None:
        raise HTTPException(400, "Unknown decision provider type.")
    allowed = {field["name"]: field for field in definition["fields"]}
    if values.keys() - allowed.keys():
        raise HTTPException(400, "Unsupported decision provider field.")
    result = {}
    for name, value in values.items():
        field = allowed[name]
        if field["input_type"] == "number":
            try:
                if isinstance(value, bool):
                    raise ValueError
                number = float(value)
                if not math.isfinite(number) or number < field.get("min", 0):
                    raise ValueError
                if field.get("step") == 1 and not number.is_integer():
                    raise ValueError
                result[name] = int(number) if field.get("step") == 1 else number
            except (TypeError, ValueError, OverflowError) as exc:
                raise HTTPException(400, f"Invalid value for {name}.") from exc
        else:
            if value is not None and not isinstance(value, str):
                raise HTTPException(400, f"Invalid value for {name}.")
            result[name] = str(value or "").strip()
            if field["input_type"] == "url" and result[name]:
                try:
                    url = urlsplit(result[name])
                    valid = url.scheme in {"http", "https"} and bool(url.netloc)
                except ValueError:
                    valid = False
                if not valid:
                    raise HTTPException(400, "Base URL must be an HTTP or HTTPS URL.")
    return result


def find_provider(service: Any, name: str) -> dict[str, Any]:
    entry = provider_entries(service.get_all()).get(name.lower())
    if entry is None:
        raise HTTPException(404, "Decision provider not found.")
    return entry


@router.get("/dashboard-api/decision-providers")
async def list_providers(request: Request) -> dict[str, Any]:
    return provider_payload(get_request_config_service(request))


@router.post("/dashboard-api/decision-providers")
async def create_provider(body: CreateBody, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    try:
        name = validate_provider_name(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if name.lower() in provider_entries(service.get_all()):
        raise HTTPException(409, "Decision provider name already exists.")
    fields = validated_fields(body.type, body.fields)
    update_config_settings(service, {f"decision.providers.{name}.type": body.type,
        **{f"decision.providers.{name}.{key}": value for key, value in fields.items()}}, require_writable=True)
    return {"status": "created", "name": name}


@router.put("/dashboard-api/decision-providers/default")
async def set_default(body: DefaultBody, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    name = ""
    if body.name:
        entry = find_provider(service, body.name)
        if not is_configured(entry):
            raise HTTPException(400, "Decision provider configuration is incomplete.")
        name = entry["name"]
    update_config_settings(service, {"decision.default_provider": name}, require_writable=True)
    return {"status": "updated", "name": name}


@router.put("/dashboard-api/decision-providers/{name}")
async def update_provider(name: str, body: UpdateBody, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    entry = find_provider(service, name)
    fields = validated_fields(entry["type"], body.fields)
    update_config_settings(service, {f"decision.providers.{entry['name']}.{key}": value
                                    for key, value in fields.items()}, require_writable=True)
    return {"status": "updated", "name": entry["name"]}


@router.delete("/dashboard-api/decision-providers/{name}")
async def delete_provider(name: str, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    entry = find_provider(service, name)
    if service.get_str("decision.default_provider", "").lower() == entry["name"].lower():
        raise HTTPException(409, "Default decision provider cannot be deleted.")
    delete_config_tree(service, f"decision.providers.{entry['name']}")
    return {"status": "deleted", "name": entry["name"]}
