from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import BaseModel

from agent.delivery.http.dashboard.routes.helpers.providers import provider_model_options
from agent.modules.agents import AgentCard, AgentConfig, get_catalog_service
from agent.modules.tools import ToolSource, find_descriptors, serialize_tool_config_schemas
from agent.modules.workflows import (
    REACT_AGENT_GRAPH_TYPE,
    ROUTER_GRAPH_TYPE,
    list_registered_workflows,
)

if TYPE_CHECKING:
    from agent.delivery.http.dashboard.routes.agents import AgentCardBody


logger = logging.getLogger(__name__)

_TOOL_CATEGORY_ORDER = (
    "file",
    "shell",
    "web",
    "image",
    "schedule",
    "agent",
    "skill",
    "utility",
    "unknown",
)


def _dump_model(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def serialize_agent_card(card: AgentCard) -> dict[str, Any]:
    return _dump_model(card)


def serialize_agent_config(config: AgentConfig) -> dict[str, Any]:
    return _dump_model(config)


def _build_tool_groups(
    tool_names: set[str],
    tool_categories: dict[str, str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for name in tool_names:
        category = tool_categories.get(name, "unknown")
        grouped.setdefault(category, []).append(name)

    ordered_categories = [c for c in _TOOL_CATEGORY_ORDER if c in grouped]
    ordered_categories += sorted(c for c in grouped if c not in _TOOL_CATEGORY_ORDER)

    return [
        {"category": category, "tools": sorted(grouped[category])}
        for category in ordered_categories
    ]


async def agent_card_options(cards: list[AgentCard] | None = None) -> dict[str, Any]:
    catalog = get_catalog_service()
    cards = cards if cards is not None else catalog.list_agent_cards()

    workflows = list_registered_workflows()
    for workflow_name in (REACT_AGENT_GRAPH_TYPE, ROUTER_GRAPH_TYPE):
        if workflow_name not in workflows:
            workflows.append(workflow_name)

    builtin_descriptors = find_descriptors(source=ToolSource.BUILTIN)
    tool_categories = {desc.name: desc.category.value for desc in builtin_descriptors}
    tool_config_schemas = serialize_tool_config_schemas(builtin_descriptors)
    tool_names = set(tool_categories)
    agent_names = []
    for card in cards:
        if not card.valid:
            continue
        agent_names.append(card.name)
        tool_names.update(
            name for name in card.tools if not name.startswith("mcp__")
        )

    tool_groups = _build_tool_groups(tool_names, tool_categories)

    try:
        from agent.modules.mcp import list_all_agent_mcp_installs, list_mcp_installs

        mcp_installs = list_mcp_installs()
        mcp_servers = [str(item.get("server_name") or "") for item in mcp_installs]
        all_agent_installs = list_all_agent_mcp_installs()
        agent_mcp_installs = {
            card.name: all_agent_installs.get(card.name, [])
            for card in cards
            if card.valid
        }
    except Exception:
        mcp_servers = []
        agent_mcp_installs = {}

    return {
        "cards": [serialize_agent_card(card) for card in cards],
        "tools": sorted(tool_names),
        "tool_groups": tool_groups,
        "tool_config_schemas": tool_config_schemas,
        "workflows": workflows,
        "agent_names": sorted(agent_names),
        "mcp_server_options": mcp_servers,
        "mcp_installs": agent_mcp_installs,
        **await provider_model_options(),
    }


def handle_agent_card_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected agent card operation failure.")
    return HTTPException(status_code=500, detail=str(exc))


def handle_prompt_variable_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected prompt variable operation failure.")
    return HTTPException(status_code=500, detail=str(exc))


def agent_config_from_body(body: "AgentCardBody") -> AgentConfig:
    return AgentConfig(
        name=body.name.strip(),
        display_name=body.display_name.strip(),
        description=body.description.strip(),
        graph_type=body.graph_type.strip() or REACT_AGENT_GRAPH_TYPE,
        provider=body.provider.strip(),
        model=body.model.strip(),
        tools=list(body.tools),
        tool_configs={
            name: dict(values)
            for name, values in body.tool_configs.items()
            if name in body.tools and isinstance(values, dict)
        },
        mcp_servers=list(body.mcp_servers) if hasattr(body, "mcp_servers") and body.mcp_servers is not None else None,
        sub_agents=list(body.sub_agents) if body.sub_agents is not None else None,
        plan_approval_targets=list(body.plan_approval_targets),
        hidden=body.hidden,
        max_context_tokens=body.max_context_tokens,
        system_prompt=body.system_prompt.strip(),
    )
