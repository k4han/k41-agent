"""Dashboard management of named decision model providers."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.settings import delete_config_tree, update_config_settings
from agent.delivery.http.dashboard.routes.helpers.urls import base_url_error
from agent.shared.config.decision_providers import (
    DECISION_PROVIDERS, effective_fields, is_configured, provider_entries, provider_payload, validate_provider_name,
)

router = APIRouter()


class VerifyDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    type: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


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
                url_err = base_url_error(result[name])
                if url_err:
                    raise HTTPException(status_code=400, detail=f"Invalid base URL: {url_err}")
    return result


def find_provider(service: Any, name: str) -> dict[str, Any]:
    entry = provider_entries(service.get_all()).get(name.lower())
    if entry is None:
        raise HTTPException(404, "Decision provider not found.")
    return entry


@router.get("/dashboard-api/decision-providers")
async def list_providers(request: Request) -> dict[str, Any]:
    return provider_payload(get_request_config_service(request))


@router.post("/dashboard-api/decision-providers/verify")
async def verify_decision_provider(body: VerifyDecisionBody, request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    prov_name = body.name.strip() if body.name else ""
    prov_type = body.type.strip().lower() if body.type else ""
    fields = dict(body.fields or {})
    explicit_type = bool(prov_type)

    if prov_name:
        entry = provider_entries(service.get_all()).get(prov_name.lower())
        if entry is not None:
            saved_type = str(entry.get("type", "")).strip().lower()
            if not prov_type:
                prov_type = saved_type
            try:
                saved_fields = effective_fields(entry)
            except ValueError:
                return {
                    "ok": False,
                    "message": f"Unknown decision provider type: {saved_type or 'None'}.",
                    "latency_ms": 0,
                    "error_code": "INVALID_CONFIG",
                    "details": None,
                }
            # Only merge saved credentials when testing the same provider type.
            # An explicit different type means a fresh candidate, not an override.
            # None means field omitted -> fall back to saved value.
            # Empty string means explicitly cleared -> do not fall back.
            if (not explicit_type) or (prov_type == saved_type):
                merged = {**saved_fields}
                for k, v in fields.items():
                    if v is None:
                        continue
                    merged[k] = v
                fields = merged
        elif not prov_type:
            return {
                "ok": False,
                "message": f"Decision provider '{prov_name}' not found.",
                "latency_ms": 0,
                "error_code": "INVALID_CONFIG",
                "details": None,
            }

    if not prov_type or prov_type not in DECISION_PROVIDERS:
        return {
            "ok": False,
            "message": f"Unknown decision provider type: {prov_type or 'None'}.",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }

    definition = DECISION_PROVIDERS[prov_type]
    if prov_type != "cloudflare":
        missing = [
            field["label"]
            for field in definition.get("fields", [])
            if field.get("required") and not str(fields.get(field["name"]) or "").strip()
        ]
        if missing:
            return {
                "ok": False,
                "message": f"{', '.join(missing)} is required for {definition.get('label', prov_type)} decision provider.",
                "latency_ms": 0,
                "error_code": "INVALID_CONFIG",
                "details": None,
            }
        return {
            "ok": False,
            "message": f"Verification is not supported for decision provider type: {prov_type}.",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }

    account_id = str(fields.get("account_id") or "").strip()
    api_token = str(fields.get("api_token") or "").strip()
    base_url = str(fields.get("base_url") or "https://api.cloudflare.com/client/v4").strip().rstrip("/")
    model = str(fields.get("model") or "@cf/cloudflare/clef-flash").strip()

    if not account_id:
        return {
            "ok": False,
            "message": "Account ID is required for Cloudflare decision provider.",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }
    if not api_token:
        return {
            "ok": False,
            "message": "API Token is required for Cloudflare decision provider.",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }
    url_err = base_url_error(base_url)
    if url_err:
        return {
            "ok": False,
            "message": f"Invalid base URL: {url_err}",
            "latency_ms": 0,
            "error_code": "INVALID_CONFIG",
            "details": None,
        }

    try:
        timeout_value = float(fields.get("timeout", 8.0) or 8.0)
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError
        timeout_value = min(max(timeout_value, 1.0), 30.0)
    except (TypeError, ValueError, OverflowError):
        timeout_value = 8.0

    # Verify against the real inference endpoint (POST ai/run) instead of
    # token-verify endpoints. A token scoped only for Workers AI can run
    # routing (POST ai/run) yet fail GET /user/tokens/verify with 403,
    # which previously produced a false "Authentication failed" while
    # routing worked. See https://developers.cloudflare.com/workers-ai/models/clef-flash/
    model_short = model.split("/")[-1].strip() or "clef-flash"
    encoded_model = "/".join(quote(part, safe="") for part in model.split("/"))
    endpoint = f"{base_url}/accounts/{quote(account_id, safe='')}/ai/run/{encoded_model}"
    probe_payload = {
        "model": model_short,
        "state": "Connection test.",
        "questions": {
            "ping": {"type": "noul", "instructions": "Is this a connection test?"},
        },
    }

    start_time = time.perf_counter()
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_value) as client:
            resp = await client.post(endpoint, json=probe_payload, headers=headers)
            if resp.status_code == 200:
                try:
                    data = resp.json() if resp.content else {}
                except Exception:
                    data = {}
                if isinstance(data, dict) and data.get("success") is False:
                    errors = data.get("errors") or data.get("messages") or "Unknown error"
                    return {
                        "ok": False,
                        "message": f"Cloudflare decision model error: {str(errors)[:200]}",
                        "latency_ms": max(1, int((time.perf_counter() - start_time) * 1000)),
                        "error_code": "CONNECTION_ERROR",
                        "details": {"status_code": 200, "model": model},
                    }
            resp.raise_for_status()

        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return {
            "ok": True,
            "message": "Cloudflare decision provider verified successfully.",
            "latency_ms": latency_ms,
            "error_code": None,
            "details": {"account_id": account_id, "model": model},
        }
    except httpx.HTTPStatusError as exc:
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        if exc.response.status_code in (401, 403):
            return {
                "ok": False,
                "message": "Authentication failed: Invalid Cloudflare API token or insufficient permissions.",
                "latency_ms": latency_ms,
                "error_code": "AUTH_FAILED",
                "details": {"status_code": exc.response.status_code},
            }
        if exc.response.status_code == 404:
            return {
                "ok": False,
                "message": f"Cloudflare API 404: check Account ID and model '{model}'. {exc.response.text[:150]}",
                "latency_ms": latency_ms,
                "error_code": "CONNECTION_ERROR",
                "details": {"status_code": 404},
            }
        return {
            "ok": False,
            "message": f"Cloudflare API HTTP {exc.response.status_code}: {exc.response.text[:200]}",
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
            "message": f"Could not connect to Cloudflare: {exc}",
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
