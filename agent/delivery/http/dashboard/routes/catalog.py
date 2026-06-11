from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.providers import PROVIDER_TYPE_OPTIONS
from agent.modules.channels import (
    ChannelStatus,
    get_registered_channel_catalog,
)
from agent.modules.prompt_variables import (
    PROMPT_VARIABLE_NAME_PATTERN,
    PromptVariableService,
)
from agent.modules.workspaces import list_workspace_backend_catalog


router = APIRouter()

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

MCP_TRANSPORT_OPTIONS: list[dict[str, str]] = [
    {"value": "stdio", "label": "stdio (command)"},
    {"value": "streamable_http", "label": "HTTP (URL)"},
]


@router.get("/dashboard-api/catalog")
async def get_catalog(request: Request) -> dict[str, Any]:
    channel_catalog = get_registered_channel_catalog()
    backend_catalog = list_workspace_backend_catalog(
        config_service=get_request_config_service(request),
    )
    return {
        "provider_types": PROVIDER_TYPE_OPTIONS,
        "channels": channel_catalog,
        "backends": backend_catalog,
        "trigger_types": TRIGGER_TYPE_OPTIONS,
        "channel_statuses": CHANNEL_STATUS_OPTIONS,
        "platforms": [
            {"value": item["name"], "label": item["title"]}
            for item in channel_catalog
        ],
        "mcp_transports": MCP_TRANSPORT_OPTIONS,
        "prompt_variable_name_pattern": f"^{PROMPT_VARIABLE_NAME_PATTERN}$",
        "system_variable_names": sorted(PromptVariableService.SYSTEM_VARIABLE_NAMES),
    }
