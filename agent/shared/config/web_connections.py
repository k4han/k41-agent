"""Named web service connections, migration, and credential resolution."""

from __future__ import annotations

import os
import re
from typing import Any

DEFAULT_CONNECTION = "__default__"
MIGRATION_KEY = "web.migration_version"
WEB_SERVICES = {
    "google": {"label": "Google Search", "capabilities": ["search"], "fields": ["api_key", "cse_id"]},
    "tavily": {"label": "Tavily", "capabilities": ["search", "fetch"], "fields": ["api_key"]},
    "firecrawl": {"label": "Firecrawl", "capabilities": ["search", "fetch"], "fields": ["api_key", "base_url"]},
    "brave": {"label": "Brave", "capabilities": ["search"], "fields": ["api_key"]},
    "bing": {"label": "Bing", "capabilities": ["search"], "fields": ["api_key"]},
}
WEB_TOOLS = {"web_search": "search", "web_fetch": "fetch"}
ENV_FIELDS = {
    ("google", "api_key"): "GOOGLE_API_KEY",
    ("google", "cse_id"): "GOOGLE_CSE_ID",
    ("tavily", "api_key"): "TAVILY_API_KEY",
    ("firecrawl", "api_key"): "FIRECRAWL_API_KEY",
    ("firecrawl", "base_url"): "FIRECRAWL_BASE_URL",
    ("brave", "api_key"): "BRAVE_API_KEY",
    ("bing", "api_key"): "BING_API_KEY",
}


def legacy_field(service_type: str, field: str) -> str:
    return f"{service_type}_{field}"


def is_legacy_web_key(key: str) -> bool:
    return any(
        key == f"tools.{tool}.{legacy_field(kind, field)}"
        for tool, capability in WEB_TOOLS.items()
        for kind, definition in WEB_SERVICES.items()
        if capability in definition["capabilities"]
        for field in definition["fields"]
    )


def validate_connection_name(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name.lower() == DEFAULT_CONNECTION:
        raise ValueError("Connection name must contain letters, numbers, underscores or hyphens.")
    return name


def connection_entries(flat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for key, value in flat.items():
        parts = key.split(".")
        if len(parts) == 4 and parts[:2] == ["web", "connections"]:
            entry = entries.setdefault(parts[2].lower(), {"name": parts[2]})
            entry[parts[3]] = value
    return entries


def selected_connection(service: Any, tool: str, kind: str, override: Any = None) -> dict[str, Any] | None:
    reference = str(override or service.get(f"tools.{tool}.{kind}_connection") or DEFAULT_CONNECTION).strip()
    if reference == DEFAULT_CONNECTION:
        reference = str(service.get(f"web.defaults.{kind}") or "").strip()
    if not reference:
        return None
    entry = connection_entries(service.get_all()).get(reference.lower())
    if entry is None or entry.get("type") != kind:
        raise ValueError(f"Invalid {kind} connection for {tool}: {reference}.")
    if WEB_TOOLS[tool] not in WEB_SERVICES[kind]["capabilities"]:
        raise ValueError(f"Connection does not support {tool}: {reference}.")
    return entry


def resolve_web_field(service: Any, tool: str, kind: str, field: str, config: dict[str, Any]) -> tuple[str, str]:
    """Resolve at invocation time, keeping agent overrides ahead of live connections."""
    override = config.get(f"{kind}_connection")
    try:
        connection = selected_connection(service, tool, kind, override)
    except ValueError:
        # Explicit agent override of a broken connection fails fast so misconfig
        # surfaces instead of silently falling back. A broken global binding for
        # an unused provider in auto mode is treated as empty so one bad binding
        # does not break the whole auto cascade; explicit provider use still raises.
        if override:
            raise
        provider = str(config.get("provider") or "auto").strip().lower() or "auto"
        if provider == kind:
            raise
        connection = None
    own = str(config.get(legacy_field(kind, field)) or "").strip()
    if own:
        return own, "override"
    if connection and str(connection.get(field) or "").strip():
        return str(connection[field]).strip(), f"connection:{connection['name']}"
    # Compatibility for configurations without an attached runtime database.
    if not service.get(MIGRATION_KEY) and connection is None:
        for candidate in (tool, "web_fetch" if tool == "web_search" else "web_search"):
            value = str(service.get(f"tools.{candidate}.{legacy_field(kind, field)}") or "").strip()
            if value:
                return value, "legacy"
    env = ENV_FIELDS[(kind, field)]
    value = os.environ.get(env, "").strip()
    return value, f"environment:{env}" if value else "default"


def migration_updates(flat: dict[str, Any]) -> dict[str, Any]:
    """Build an idempotent migration without reading process credentials."""
    if flat.get(MIGRATION_KEY):
        return {}
    updates: dict[str, Any] = {}
    entries = connection_entries(flat)
    for kind, definition in WEB_SERVICES.items():
        profiles: dict[tuple[str, ...], str] = {}
        for tool, capability in WEB_TOOLS.items():
            if capability not in definition["capabilities"]:
                continue
            peer = "web_fetch" if tool == "web_search" else "web_search"
            values = tuple(
                str(flat.get(f"tools.{tool}.{legacy_field(kind, field)}") or "").strip() or
                str(flat.get(f"tools.{peer}.{legacy_field(kind, field)}") or "").strip()
                for field in definition["fields"]
            )
            binding = f"tools.{tool}.{kind}_connection"
            if not any(values) or binding in flat:
                continue
            if values not in profiles:
                existing = next((entry for entry in entries.values()
                                 if entry.get("type") == kind and
                                 tuple(str(entry.get(field) or "").strip() for field in definition["fields"]) == values), None)
                if existing:
                    name = existing["name"]
                else:
                    base = f"{kind}-legacy-{tool.replace('_', '-')}"
                    name = base
                    suffix = 2
                    while name.lower() in entries:
                        name = f"{base}-{suffix}"
                        suffix += 1
                    entry = {"name": name, "type": kind, **dict(zip(definition["fields"], values))}
                    entries[name.lower()] = entry
                    updates.update({f"web.connections.{name}.{field}": value
                                    for field, value in entry.items() if field != "name"})
                profiles[values] = name
            name = profiles[values]
            default_key = f"web.defaults.{kind}"
            default_name = flat.get(default_key) or updates.get(default_key)
            if not default_name:
                updates[default_key] = name
                default_name = name
            updates[binding] = DEFAULT_CONNECTION if str(default_name).lower() == name.lower() else name
    updates[MIGRATION_KEY] = 1
    return updates


def connection_payload(service: Any) -> dict[str, Any]:
    flat = service.get_all()
    defaults = {kind: str(flat.get(f"web.defaults.{kind}") or "") for kind in WEB_SERVICES}
    rows = []
    for entry in sorted(connection_entries(flat).values(), key=lambda item: item["name"].lower()):
        kind = entry.get("type")
        if kind not in WEB_SERVICES:
            continue
        definition = WEB_SERVICES[kind]
        fields = {field: str(entry.get(field) or "") for field in definition["fields"] if field != "api_key"}
        effective = {field: str(entry.get(field) or "").strip() or os.environ.get(ENV_FIELDS[(kind, field)], "").strip()
                     for field in definition["fields"]}
        rows.append({"name": entry["name"], "type": kind, "fields": fields,
                     "has_api_key": bool(str(entry.get("api_key") or "").strip()),
                     "configured": bool((effective["api_key"] or (kind == "firecrawl" and effective.get("base_url")))
                                        and (kind != "google" or effective.get("cse_id"))),
                     "is_default": defaults[kind].lower() == entry["name"].lower(),
                     "capabilities": definition["capabilities"],
                     "sources": {field: "connection" if str(entry.get(field) or "").strip() else
                                 "environment" if os.environ.get(ENV_FIELDS[(kind, field)]) else "default"
                                 for field in definition["fields"]}})
    return {"connections": rows, "defaults": defaults,
            "services": [{"type": kind, **definition} for kind, definition in WEB_SERVICES.items()]}


def validate_tool_references(service: Any, tool: str, values: dict[str, Any]) -> None:
    if tool not in WEB_TOOLS:
        return
    for field, reference in values.items():
        if not field.endswith("_connection") or not reference:
            continue
        kind = field.removesuffix("_connection")
        if kind not in WEB_SERVICES or WEB_TOOLS[tool] not in WEB_SERVICES[kind]["capabilities"]:
            raise ValueError(f"Unsupported connection field for {tool}: {field}.")
        selected_connection(service, tool, kind, reference)


def translate_legacy_updates(service: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Fork legacy credential edits into a tool-specific named connection."""
    output = {key: value for key, value in values.items() if not is_legacy_web_key(key)}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for key, value in values.items():
        if not is_legacy_web_key(key):
            continue
        _, tool, field = key.split(".")
        kind, suffix = field.split("_", 1)
        groups.setdefault((tool, kind), {})[suffix] = value
    flat = service.get_all()
    entries = connection_entries(flat)
    for (tool, kind), edits in groups.items():
        selected = selected_connection(service, tool, kind)
        definition = WEB_SERVICES[kind]
        fields = {field: (selected or {}).get(field, "") for field in definition["fields"]}
        if not selected and not service.get(MIGRATION_KEY):
            peer = "web_fetch" if tool == "web_search" else "web_search"
            fields = {field: flat.get(f"tools.{tool}.{legacy_field(kind, field)}") or
                      flat.get(f"tools.{peer}.{legacy_field(kind, field)}") or ""
                      for field in definition["fields"]}
        fields.update(edits)
        base = f"{kind}-legacy-edit-{tool.replace('_', '-')}"
        name = base
        index = 2
        current_ref = str(flat.get(f"tools.{tool}.{kind}_connection") or "")
        protected = {str(value).lower() for key, value in flat.items()
                     if key.startswith("web.defaults.") or
                     (key.startswith("tools.") and key.endswith("_connection") and key != f"tools.{tool}.{kind}_connection")}
        if current_ref.lower() in entries:
            from agent.modules.agents import get_catalog_service
            protected.update(str(value).lower() for card in get_catalog_service().list_agent_cards()
                             for config in card.tool_configs.values() for field, value in config.items()
                             if field.endswith("_connection"))
        while name.lower() in entries and (current_ref.lower() != name.lower() or name.lower() in protected):
            name = f"{base}-{index}"
            index += 1
        output[f"web.connections.{name}.type"] = kind
        output.update({f"web.connections.{name}.{field}": value or "" for field, value in fields.items()})
        output[f"tools.{tool}.{kind}_connection"] = name
    return output


def with_web_connections(schema: Any, tool: str) -> Any:
    from dataclasses import replace
    from agent.modules.tools.domain import ToolConfigField

    fields = tuple(ToolConfigField(
        name=f"{kind}_connection", input_type="select", label=f"{definition['label']} connection",
        description="Use the shared default or select a named connection.", default=DEFAULT_CONNECTION,
        show_when={"provider": ("auto", kind)},
    ) for kind, definition in WEB_SERVICES.items() if WEB_TOOLS[tool] in definition["capabilities"])
    existing = tuple(replace(field, description="Optional agent credential override. Inherits the selected web connection, then environment credentials, when empty.")
                     if is_legacy_web_key(f"tools.{tool}.{field.name}") else field for field in schema.fields)
    return replace(schema, fields=existing + fields)


def web_tool_schemas(schemas: dict[str, Any], service: Any, *, global_view: bool = False) -> dict[str, Any]:
    entries = connection_entries(service.get_all())
    result = dict(schemas)
    for tool in WEB_TOOLS:
        if tool not in schemas:
            continue
        fields = []
        for original in schemas[tool]["fields"]:
            field = dict(original)
            if global_view and is_legacy_web_key(f"tools.{tool}.{field['name']}"):
                continue
            if field["name"].endswith("_connection"):
                kind = field["name"].removesuffix("_connection")
                field["options"] = [DEFAULT_CONNECTION, *sorted(
                    entry["name"] for entry in entries.values() if entry.get("type") == kind)]
            fields.append(field)
        result[tool] = {**schemas[tool], "fields": fields}
    return result


def web_tool_sources(service: Any) -> dict[str, dict[str, str]]:
    result = {}
    for tool, capability in WEB_TOOLS.items():
        sources = {}
        for kind, definition in WEB_SERVICES.items():
            if capability not in definition["capabilities"]:
                continue
            for field in definition["fields"]:
                _, source = resolve_web_field(service, tool, kind, field, {})
                sources[legacy_field(kind, field)] = source
        result[tool] = sources
    return result
