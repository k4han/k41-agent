"""Named decision providers, credential resolution, and legacy migration."""

from __future__ import annotations

import os
import re
from typing import Any

MIGRATION_KEY = "decision.migration_version"
DECISION_PROVIDERS: dict[str, dict[str, Any]] = {
    "cloudflare": {
        "label": "Cloudflare",
        "fields": [
            {"name": "account_id", "label": "Account ID", "input_type": "text", "required": True},
            {"name": "api_token", "label": "API Token", "input_type": "password", "required": True},
            {"name": "base_url", "label": "Base URL", "input_type": "url", "default": "https://api.cloudflare.com/client/v4"},
            {"name": "model", "label": "Model", "input_type": "text", "required": True, "default": "@cf/cloudflare/clef-flash"},
            {"name": "timeout", "label": "Timeout (seconds)", "input_type": "number", "min": 0.1, "default": 5.0},
            {"name": "max_retries", "label": "Maximum retries", "input_type": "number", "min": 0, "step": 1, "default": 2},
        ],
    },
}
ENV_FIELDS = {"account_id": "CLOUDFLARE_ACCOUNT_ID", "api_token": "CLOUDFLARE_API_TOKEN"}


def validate_provider_name(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name.lower() == "default":
        raise ValueError("Provider name must contain letters, numbers, underscores or hyphens and cannot be 'default'.")
    return name


def provider_entries(flat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for key, value in flat.items():
        parts = key.split(".")
        if len(parts) == 4 and parts[:2] == ["decision", "providers"]:
            entries.setdefault(parts[2].lower(), {"name": parts[2]})[parts[3]] = value
    return entries


def effective_fields(entry: dict[str, Any]) -> dict[str, Any]:
    definition = DECISION_PROVIDERS.get(entry.get("type", ""))
    if definition is None:
        raise ValueError("Unknown decision provider type.")
    fields = {}
    for field in definition["fields"]:
        name = field["name"]
        value = entry.get(name)
        if isinstance(value, str):
            value = value.strip()
        if value is None or value == "":
            value = field.get("default", "")
            if entry["type"] == "cloudflare" and name in ENV_FIELDS:
                value = os.environ.get(ENV_FIELDS[name], "").strip()
        fields[name] = value
    return fields


def is_configured(entry: dict[str, Any]) -> bool:
    if entry.get("type") not in DECISION_PROVIDERS:
        return False
    fields = effective_fields(entry)
    return all(str(fields.get(field["name"], "")).strip()
               for field in DECISION_PROVIDERS[entry["type"]]["fields"] if field.get("required"))


def resolve_provider(config: Any) -> dict[str, Any]:
    name = config.get_str("decision.default_provider", "").strip()
    if name:
        entry = provider_entries(config.get_all()).get(name.lower())
        if entry is None:
            raise ValueError(f"Decision provider not found: {name}.")
        return {"name": entry["name"], "type": entry.get("type"), **effective_fields(entry)}
    # Retain legacy/environment behavior until a named provider is selected.
    entry = {"name": "", "type": "cloudflare"}
    for field in DECISION_PROVIDERS["cloudflare"]["fields"]:
        key = field["name"]
        legacy = f"decision.{key}" if key in {"model", "timeout", "max_retries"} else f"decision.cloudflare.{key}"
        value = config.get(legacy)
        if value is None or value == "":
            value = config.get(f"decision.cloudflare.{key}")
        if (value is None or value == "") and key in {"base_url", "model"}:
            value = os.environ.get(f"CLOUDFLARE_{key.upper()}", "").strip()
        entry[key] = value
    return {"name": "", "type": "cloudflare", **effective_fields(entry)}


def provider_payload(config: Any) -> dict[str, Any]:
    default = config.get_str("decision.default_provider", "").strip()
    providers = []
    for entry in sorted(provider_entries(config.get_all()).values(), key=lambda item: item["name"].lower()):
        definition = DECISION_PROVIDERS.get(entry.get("type"), {"fields": []})
        secrets = {field["name"] for field in definition["fields"] if field["input_type"] == "password"}
        providers.append({
            "name": entry["name"], "type": entry.get("type", ""),
            "fields": {field["name"]: entry.get(field["name"], field.get("default", ""))
                       for field in definition["fields"] if field["name"] not in secrets},
            "has_api_token": bool(entry.get("api_token")),
            "stored_secrets": {name: bool(entry.get(name)) for name in secrets},
            "configured": is_configured(entry),
            "is_default": entry["name"].lower() == default.lower(),
        })
    return {"providers": providers, "default_provider": default,
            "services": [{"type": kind, **definition} for kind, definition in DECISION_PROVIDERS.items()]}


def migration_updates(flat: dict[str, Any]) -> dict[str, Any]:
    if flat.get(MIGRATION_KEY):
        return {}
    updates: dict[str, Any] = {MIGRATION_KEY: 1}
    if flat.get("decision.default_provider"):
        return updates
    credentials = {key: flat.get(f"decision.cloudflare.{key}", "") for key in ENV_FIELDS}
    if not any(credentials.values()):
        return updates
    entries = provider_entries(flat)
    name = "cloudflare-legacy"
    suffix = 2
    while name.lower() in entries:
        name = f"cloudflare-legacy-{suffix}"
        suffix += 1
    updates[f"decision.providers.{name}.type"] = "cloudflare"
    for field in DECISION_PROVIDERS["cloudflare"]["fields"]:
        key = field["name"]
        legacy = f"decision.{key}" if key in {"model", "timeout", "max_retries"} else f"decision.cloudflare.{key}"
        value = flat.get(legacy, flat.get(f"decision.cloudflare.{key}", field.get("default", "")))
        updates[f"decision.providers.{name}.{key}"] = value
    updates["decision.default_provider"] = name
    return updates
