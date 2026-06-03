from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agent.delivery.http.dashboard.routes.shared import PROVIDER_TYPE_OPTIONS
from agent.modules.channels.manager import ChannelStatus
from agent.modules.channels.service_specs import BUILTIN_CHANNEL_SPECS
from agent.modules.workspaces.refs import WorkspaceBackendName


router = APIRouter()

CHANNEL_CATALOG: list[dict[str, Any]] = [
    {
        "name": spec.name,
        "title": spec.name.title(),
        "required_env": list(spec.required_env),
    }
    for spec in BUILTIN_CHANNEL_SPECS
]

BACKEND_CATALOG: list[dict[str, Any]] = [
    {"name": name, "title": name.title()}
    for name in WorkspaceBackendName.__args__
]

TRIGGER_TYPE_OPTIONS: list[dict[str, str]] = [
    {"value": "date", "label": "Run once at a time"},
    {"value": "relative", "label": "Run once after a delay"},
    {"value": "interval", "label": "Run every interval"},
    {"value": "cron", "label": "Cron schedule"},
]

CHANNEL_STATUS_OPTIONS: list[dict[str, str]] = [
    {"value": status.value, "label": status.value.replace("_", " ").title()}
    for status in ChannelStatus
]

PLATFORM_OPTIONS: list[dict[str, str]] = [
    {"value": spec.name, "label": spec.name}
    for spec in BUILTIN_CHANNEL_SPECS
]


@router.get("/dashboard-api/catalog")
async def get_catalog() -> dict[str, Any]:
    return {
        "provider_types": PROVIDER_TYPE_OPTIONS,
        "channels": CHANNEL_CATALOG,
        "backends": BACKEND_CATALOG,
        "trigger_types": TRIGGER_TYPE_OPTIONS,
        "channel_statuses": CHANNEL_STATUS_OPTIONS,
        "platforms": PLATFORM_OPTIONS,
    }
