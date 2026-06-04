from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.modules.workflows import REACT_AGENT_GRAPH_TYPE
from agent.delivery.http.dashboard.routes.shared import (
    _agent_card_options,
    _agent_config_from_body,
    _handle_agent_card_error,
    _handle_prompt_variable_error,
    _serialize_agent_card,
)
from agent.modules.agents import get_catalog_service
from agent.modules.prompt_variables import get_prompt_variable_service


router = APIRouter()


class AgentCardBody(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str = REACT_AGENT_GRAPH_TYPE
    provider: str = "default"
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    sub_agents: list[str] | None = None
    plan_approval_targets: list[str] = Field(default_factory=list)
    hidden: bool = False
    max_context_tokens: int = 50_000
    system_prompt: str = ""


@router.get("/agents/cards")
async def list_agent_cards() -> dict[str, Any]:
    return await _agent_card_options()


@router.post("/agents/cards")
async def create_agent_card(body: AgentCardBody) -> dict[str, Any]:
    catalog = get_catalog_service()
    try:
        card = catalog.create_agent_card(_agent_config_from_body(body))
    except Exception as exc:
        raise _handle_agent_card_error(exc) from exc
    return {"status": "created", "card": _serialize_agent_card(card)}


@router.put("/agents/cards/{name}")
async def update_agent_card(name: str, body: AgentCardBody) -> dict[str, Any]:
    catalog = get_catalog_service()
    try:
        card = catalog.update_agent_card(name, _agent_config_from_body(body))
    except Exception as exc:
        raise _handle_agent_card_error(exc) from exc
    return {"status": "updated", "card": _serialize_agent_card(card)}


@router.delete("/agents/cards/{name}")
async def delete_agent_card(name: str) -> dict[str, str]:
    catalog = get_catalog_service()
    try:
        catalog.delete_agent_card(name)
    except Exception as exc:
        raise _handle_agent_card_error(exc) from exc
    return {"status": "deleted", "name": name}


@router.post("/agents/cards/{name}/clone")
async def clone_builtin_agent_card(name: str) -> dict[str, Any]:
    catalog = get_catalog_service()
    try:
        card = catalog.clone_builtin_agent(name)
    except Exception as exc:
        raise _handle_agent_card_error(exc) from exc
    return {"status": "cloned", "card": _serialize_agent_card(card)}


@router.post("/agents/reload")
async def reload_agent_cards() -> dict[str, Any]:
    catalog = get_catalog_service()
    catalog.reload_agents()
    return {"status": "reloaded", **await _agent_card_options()}


class PromptVariableBody(BaseModel):
    name: str
    value: str = ""


@router.get("/dashboard-api/prompt-variables")
async def get_dashboard_prompt_variables() -> dict[str, Any]:
    service = get_prompt_variable_service()
    return {"variables": await service.list_variables()}


@router.post("/prompt-variables")
async def create_prompt_variable(body: PromptVariableBody) -> dict[str, Any]:
    service = get_prompt_variable_service()
    try:
        variable = await service.create_variable(
            name=body.name,
            value=body.value,
        )
    except Exception as exc:
        raise _handle_prompt_variable_error(exc) from exc
    return {"status": "created", "variable": variable}


@router.put("/prompt-variables/{name}")
async def update_prompt_variable(
    name: str,
    body: PromptVariableBody,
) -> dict[str, Any]:
    service = get_prompt_variable_service()
    try:
        variable = await service.update_variable(
            current_name=name,
            name=body.name,
            value=body.value,
        )
    except Exception as exc:
        raise _handle_prompt_variable_error(exc) from exc
    return {"status": "updated", "variable": variable}


@router.delete("/prompt-variables/{name}")
async def delete_prompt_variable(name: str) -> dict[str, str]:
    service = get_prompt_variable_service()
    try:
        await service.delete_variable(name)
    except Exception as exc:
        raise _handle_prompt_variable_error(exc) from exc
    return {"status": "deleted", "name": name}
