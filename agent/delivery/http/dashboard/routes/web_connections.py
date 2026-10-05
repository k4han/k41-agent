"""Dashboard management of named web service connections."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
import httpx
from pydantic import BaseModel, ConfigDict

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.settings import delete_config_tree, update_config_settings
from agent.delivery.http.dashboard.routes.helpers.urls import base_url_error
from agent.shared.config.web_connections import (
    WEB_SERVICES, WEB_TOOLS, connection_entries, connection_payload, validate_connection_name,
)

router = APIRouter()


class VerifyConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    type: str | None = None
    api_key: str | None = None
    cse_id: str | None = None
    base_url: str | None = None


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
        url_err = base_url_error(normalized["base_url"])
        if url_err:
            raise HTTPException(status_code=400, detail=f"Invalid base URL: {url_err}")
    return normalized


def find_connection(service: Any, name: str) -> dict[str, Any]:
    entry = connection_entries(service.get_all()).get(name.lower())
    if entry is None:
        raise HTTPException(404, "Web connection not found.")
    return entry


@router.get("/dashboard-api/web-connections")
async def list_connections(request: Request) -> dict[str, Any]:
    return connection_payload(get_request_config_service(request))


@router.post("/dashboard-api/web-connections/verify")
async def verify_web_connection(body: VerifyConnectionBody, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    conn_name = body.name.strip() if body.name else ""
    conn_type = body.type.strip().lower() if body.type else ""
    explicit_type = bool(body.type and body.type.strip())
    api_key = body.api_key.strip() if body.api_key is not None else None
    cse_id = body.cse_id.strip() if body.cse_id is not None else None
    base_url = body.base_url.strip() if body.base_url is not None else None

    if conn_name:
        entry = connection_entries(service.get_all()).get(conn_name.lower())
        if entry is not None:
            saved_type = str(entry.get("type", "")).strip().lower()
            if not conn_type:
                conn_type = saved_type
            # Only reuse saved credentials when testing the same service type.
            if (not explicit_type) or (conn_type == saved_type):
                if api_key is None:
                    api_key = str(entry.get("api_key", "") or "").strip()
                if cse_id is None:
                    cse_id = str(entry.get("cse_id", "") or "").strip()
                if base_url is None:
                    base_url = str(entry.get("base_url", "") or "").strip()
        elif not conn_type:
            return {
                "ok": False,
                "message": f"Web connection '{conn_name}' not found.",
                "latency_ms": 0,
                "error_code": "INVALID_CONFIG",
                "details": None,
            }

    if not conn_type or conn_type not in WEB_SERVICES:
        return {
            "ok": False,
            "message": f"Unknown web service type: {conn_type or 'None'}.",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }

    api_key = api_key or ""
    cse_id = cse_id or ""
    base_url = base_url or ""

    if not api_key and conn_type in ("google", "tavily", "brave", "bing"):
        return {
            "ok": False,
            "message": "API key is required.",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }
    if conn_type == "google" and not cse_id:
        return {
            "ok": False,
            "message": "Search Engine ID (cse_id) is required for Google Custom Search.",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }

    if base_url:
        url_err = base_url_error(base_url)
        if url_err:
            return {
                "ok": False,
                "message": f"Invalid base URL: {url_err}",
                "latency_ms": 0,
                "error_code": "INVALID_CONFIG",
                "details": None,
            }

    start_time = time.perf_counter()
    headers = {"User-Agent": "kaka-agent-web-test/1.0"}
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
            if conn_type == "tavily":
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": "ping", "max_results": 1},
                )
                resp.raise_for_status()
            elif conn_type == "google":
                resp = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"key": api_key, "cx": cse_id, "q": "ping", "num": 1},
                )
                resp.raise_for_status()
            elif conn_type == "brave":
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": "ping", "count": 1},
                    headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                )
                resp.raise_for_status()
            elif conn_type == "bing":
                resp = await client.get(
                    "https://api.bing.microsoft.com/v7.0/search",
                    params={"q": "ping", "count": 1},
                    headers={"Ocp-Apim-Subscription-Key": api_key},
                )
                resp.raise_for_status()
            elif conn_type == "firecrawl":
                target_url = (base_url.rstrip("/") if base_url else "https://api.firecrawl.dev") + "/v1/team/credit-usage"
                firecrawl_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                resp = await client.get(target_url, headers=firecrawl_headers)
                if resp.status_code == 404:
                    search_url = (base_url.rstrip("/") if base_url else "https://api.firecrawl.dev") + "/v2/search"
                    resp = await client.post(
                        search_url,
                        json={"query": "ping", "limit": 1},
                        headers=firecrawl_headers,
                    )
                resp.raise_for_status()

        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return {
            "ok": True,
            "message": f"Connection to {WEB_SERVICES[conn_type]['label']} verified successfully.",
            "latency_ms": latency_ms,
            "error_code": None,
            "details": {"type": conn_type},
        }
    except httpx.HTTPStatusError as exc:
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        if exc.response.status_code in (401, 403):
            return {
                "ok": False,
                "message": "Authentication failed: Invalid credentials.",
                "latency_ms": latency_ms,
                "error_code": "AUTH_FAILED",
                "details": {"status_code": exc.response.status_code},
            }
        return {
            "ok": False,
            "message": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            "latency_ms": latency_ms,
            "error_code": "CONNECTION_ERROR",
            "details": {"status_code": exc.response.status_code},
        }
    except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError):
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return {
            "ok": False,
            "message": "Connection timed out.",
            "latency_ms": latency_ms,
            "error_code": "TIMEOUT",
            "details": None,
        }
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return {
            "ok": False,
            "message": f"Could not connect to service: {exc}",
            "latency_ms": latency_ms,
            "error_code": "CONNECTION_ERROR",
            "details": None,
        }
    except Exception as exc:
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return {
            "ok": False,
            "message": f"Verification error: {exc}",
            "latency_ms": latency_ms,
            "error_code": "CONNECTION_ERROR",
            "details": None,
        }


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
