from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.modules.workflows import REACT_AGENT_GRAPH_TYPE
from agent.delivery.http.dashboard.routes.helpers.agents import (
    agent_card_options,
    agent_config_from_body,
    handle_agent_card_error,
    handle_prompt_variable_error,
    serialize_agent_card,
)
from agent.modules.agents import get_catalog_service
from agent.modules.prompt_variables import get_prompt_variable_service


router = APIRouter()


class AgentCardBody(BaseModel):
    """Request body for creating or updating an agent card."""

    name: str = Field(..., description="Unique agent identifier name.")
    display_name: str = Field(default="", description="Human-readable display name.")
    description: str = Field(default="", description="Description of what this agent does.")
    graph_type: str = Field(default=REACT_AGENT_GRAPH_TYPE, description="Workflow graph type (e.g. 'react_agent').")
    provider: str = Field(default="default", description="LLM provider name.")
    model: str = Field(default="", description="LLM model name override.")
    tools: list[str] = Field(default_factory=list, description="List of tool names available to this agent.")
    mcp_servers: list[str] = Field(default_factory=list, description="List of MCP server names to use.")
    sub_agents: list[str] | None = Field(default=None, description="List of sub-agent names for delegation.")
    plan_approval_targets: list[str] = Field(default_factory=list, description="Plan step types requiring human approval.")
    hidden: bool = Field(default=False, description="Whether to hide this agent from the UI.")
    max_context_tokens: int = Field(default=50_000, description="Maximum context window size in tokens.")
    system_prompt: str = Field(default="", description="Custom system prompt for this agent.")


@router.get("/agents/cards")
async def list_agent_cards() -> dict[str, Any]:
    """List all agent cards with their configuration options."""
    return await agent_card_options()


@router.post("/agents/cards")
async def create_agent_card(body: AgentCardBody) -> dict[str, Any]:
    """Create a new agent card with the given configuration."""
    catalog = get_catalog_service()
    try:
        card = catalog.create_agent_card(agent_config_from_body(body))
    except Exception as exc:
        raise handle_agent_card_error(exc) from exc
    return {"status": "created", "card": serialize_agent_card(card)}


@router.put("/agents/cards/{name}")
async def update_agent_card(name: str, body: AgentCardBody) -> dict[str, Any]:
    """Update an existing agent card configuration."""
    catalog = get_catalog_service()
    try:
        card = catalog.update_agent_card(name, agent_config_from_body(body))
    except Exception as exc:
        raise handle_agent_card_error(exc) from exc
    return {"status": "updated", "card": serialize_agent_card(card)}


@router.delete("/agents/cards/{name}")
async def delete_agent_card(name: str) -> dict[str, str]:
    """Delete an agent card by name."""
    catalog = get_catalog_service()
    try:
        catalog.delete_agent_card(name)
    except Exception as exc:
        raise handle_agent_card_error(exc) from exc
    return {"status": "deleted", "name": name}


@router.post("/agents/cards/{name}/clone")
async def clone_builtin_agent_card(name: str) -> dict[str, Any]:
    """Clone a built-in agent card as a new user-created card."""
    catalog = get_catalog_service()
    try:
        card = catalog.clone_builtin_agent(name)
    except Exception as exc:
        raise handle_agent_card_error(exc) from exc
    return {"status": "cloned", "card": serialize_agent_card(card)}


@router.post("/agents/reload")
async def reload_agent_cards() -> dict[str, Any]:
    """Reload all agent cards from disk and return updated options."""
    catalog = get_catalog_service()
    catalog.reload_agents()
    return {"status": "reloaded", **await agent_card_options()}


class PromptVariableBody(BaseModel):
    """Request body for creating or updating a prompt variable."""

    name: str = Field(..., description="Variable name (used as the template key).")
    value: str = Field(default="", description="Variable value to substitute in prompts.")


@router.get("/dashboard-api/prompt-variables")
async def get_dashboard_prompt_variables() -> dict[str, Any]:
    """List all prompt variables."""
    service = get_prompt_variable_service()
    return {"variables": await service.list_variables()}


@router.post("/prompt-variables")
async def create_prompt_variable(body: PromptVariableBody) -> dict[str, Any]:
    """Create a new prompt variable."""
    service = get_prompt_variable_service()
    try:
        variable = await service.create_variable(
            name=body.name,
            value=body.value,
        )
    except Exception as exc:
        raise handle_prompt_variable_error(exc) from exc
    return {"status": "created", "variable": variable}


@router.put("/prompt-variables/{name}")
async def update_prompt_variable(
    name: str,
    body: PromptVariableBody,
) -> dict[str, Any]:
    """Update an existing prompt variable."""
    service = get_prompt_variable_service()
    try:
        variable = await service.update_variable(
            current_name=name,
            name=body.name,
            value=body.value,
        )
    except Exception as exc:
        raise handle_prompt_variable_error(exc) from exc
    return {"status": "updated", "variable": variable}


@router.delete("/prompt-variables/{name}")
async def delete_prompt_variable(name: str) -> dict[str, str]:
    """Delete a prompt variable by name."""
    service = get_prompt_variable_service()
    try:
        await service.delete_variable(name)
    except Exception as exc:
        raise handle_prompt_variable_error(exc) from exc
    return {"status": "deleted", "name": name}
