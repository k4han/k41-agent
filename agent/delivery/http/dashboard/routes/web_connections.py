"""Dashboard management of named web service connections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.settings import delete_config_tree, update_config_settings
from agent.shared.config.web_connections import (
    WEB_SERVICES, WEB_TOOLS, connection_entries, connection_payload, validate_connection_name,
)

router = APIRouter()


class ConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str
    api_key: str | None = None
    cse_id: str | None = None
    base_url: str | None = None


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str | None = None
    cse_id: str | None = None
    base_url: str | None = None


class DefaultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None


def validated_fields(kind: str, fields: dict[str, Any]) -> dict[str, str]:
    if kind not in WEB_SERVICES:
        raise HTTPException(400, "Unknown web service type.")
    if set(fields) - set(WEB_SERVICES[kind]["fields"]):
        raise HTTPException(400, "Unsupported connection field.")
    normalized = {key: str(value or "").strip() for key, value in fields.items()}
    if normalized.get("base_url"):
        from urllib.parse import urlsplit
        url = urlsplit(normalized["base_url"])
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise HTTPException(400, "Base URL must be an HTTP or HTTPS URL.")
    return normalized


def find_connection(service: Any, name: str) -> dict[str, Any]:
    entry = connection_entries(service.get_all()).get(name.lower())
    if entry is None:
        raise HTTPException(404, "Web connection not found.")
    return entry


@router.get("/dashboard-api/web-connections")
async def list_connections(request: Request) -> dict[str, Any]:
    return connection_payload(get_request_config_service(request))


@router.post("/dashboard-api/web-connections")
async def create_connection(body: ConnectionBody, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    try:
        name = validate_connection_name(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if name.lower() in connection_entries(service.get_all()):
        raise HTTPException(409, "Web connection name already exists.")
    fields = validated_fields(body.type, body.model_dump(exclude_unset=True, exclude={"name", "type"}))
    values = {f"web.connections.{name}.type": body.type,
              **{f"web.connections.{name}.{field}": value for field, value in fields.items()}}
    update_config_settings(service, values, require_writable=True)
    return {"status": "created", "name": name}


@router.put("/dashboard-api/web-connections/defaults/{kind}")
async def set_default(kind: str, body: DefaultBody, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    if kind not in WEB_SERVICES:
        raise HTTPException(400, "Unknown web service type.")
    name = ""
    if body.name:
        entry = find_connection(service, body.name)
        if entry["type"] != kind:
            raise HTTPException(400, "Default connection has the wrong service type.")
        name = entry["name"]
    update_config_settings(service, {f"web.defaults.{kind}": name}, require_writable=True)
    return {"status": "updated", "name": name}


@router.put("/dashboard-api/web-connections/{name}")
async def update_connection(name: str, body: ConnectionUpdate, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    entry = find_connection(service, name)
    fields = validated_fields(entry["type"], body.model_dump(exclude_unset=True))
    if fields:
        update_config_settings(service, {f"web.connections.{entry['name']}.{field}": value
                                         for field, value in fields.items()}, require_writable=True)
    return {"status": "updated", "name": entry["name"]}


@router.delete("/dashboard-api/web-connections/{name}")
async def delete_connection(name: str, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    entry = find_connection(service, name)
    references = [key for key, value in service.get_all().items()
                  if (key.startswith("web.defaults.") or
                      (key.startswith("tools.") and key.endswith("_connection")))
                  and str(value).lower() == entry["name"].lower()]
    from agent.modules.agents import get_catalog_service
    for card in get_catalog_service().list_agent_cards():
        for tool, config in (card.tool_configs or {}).items():
            for field, value in config.items():
                if tool in WEB_TOOLS and field.endswith("_connection") and str(value).lower() == entry["name"].lower():
                    references.append(f"agents.{card.name}.{tool}.{field}")
    if references:
        raise HTTPException(409, {"message": "Connection is still in use: " + ", ".join(references), "references": references})
    delete_config_tree(service, f"web.connections.{entry['name']}")
    return {"status": "deleted", "name": entry["name"]}
