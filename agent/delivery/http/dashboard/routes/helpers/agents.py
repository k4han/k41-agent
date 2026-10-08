from __future__ import annotations

import asyncio
import logging
import time
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
_agent_cards_cache: dict[str, Any] | None = None
_agent_tools_cache: dict[str, Any] | None = None
_agent_workflows_cache: dict[str, Any] | None = None
_agent_mcp_cache: dict[str, Any] | None = None
_agent_provider_options_cache: dict[str, Any] | None = None
_agent_cards_cache_version = 0
_agent_tools_cache_version = 0
_agent_workflows_cache_version = 0
_agent_mcp_cache_version = 0
_agent_provider_options_cache_version = 0
_agent_cards_lock = asyncio.Lock()
_agent_tools_lock = asyncio.Lock()
_agent_workflows_lock = asyncio.Lock()
_agent_mcp_lock = asyncio.Lock()
_agent_provider_options_lock = asyncio.Lock()

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


def _payload_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.copy()


def invalidate_agent_options_caches() -> None:
    invalidate_agent_cards_cache()
    invalidate_agent_tools_cache()
    invalidate_agent_workflows_cache()
    invalidate_agent_mcp_cache()
    invalidate_agent_provider_options_cache()


def invalidate_agent_card_related_caches() -> None:
    invalidate_agent_cards_cache()
    invalidate_agent_tools_cache()
    invalidate_agent_mcp_cache()


def invalidate_agent_cards_cache() -> None:
    global _agent_cards_cache, _agent_cards_cache_version
    _agent_cards_cache = None
    _agent_cards_cache_version += 1


def invalidate_agent_tools_cache() -> None:
    global _agent_tools_cache, _agent_tools_cache_version
    _agent_tools_cache = None
    _agent_tools_cache_version += 1


def invalidate_agent_workflows_cache() -> None:
    global _agent_workflows_cache, _agent_workflows_cache_version
    _agent_workflows_cache = None
    _agent_workflows_cache_version += 1


def invalidate_agent_mcp_cache() -> None:
    global _agent_mcp_cache, _agent_mcp_cache_version
    _agent_mcp_cache = None
    _agent_mcp_cache_version += 1


def invalidate_agent_provider_options_cache() -> None:
    global _agent_provider_options_cache, _agent_provider_options_cache_version
    _agent_provider_options_cache = None
    _agent_provider_options_cache_version += 1


async def warm_agent_options_caches() -> None:
    await asyncio.gather(
        agent_cards_payload(),
        agent_tools_payload(),
        agent_workflows_payload(),
        agent_mcp_payload(),
        agent_provider_options_payload(),
    )


async def agent_cards_payload() -> dict[str, Any]:
    global _agent_cards_cache

    if _agent_cards_cache is not None:
        return _payload_snapshot(_agent_cards_cache)

    async with _agent_cards_lock:
        if _agent_cards_cache is not None:
            return _payload_snapshot(_agent_cards_cache)

        cache_version = _agent_cards_cache_version
        started = time.perf_counter()
        cards = get_catalog_service().list_agent_cards()
        agent_names = sorted(card.name for card in cards if card.valid)
        payload = {
            "cards": [serialize_agent_card(card) for card in cards],
            "agent_names": agent_names,
        }
        if cache_version == _agent_cards_cache_version:
            _agent_cards_cache = payload
        logger.debug(
            "Built agent cards payload in %.1fms",
            (time.perf_counter() - started) * 1000,
        )
        return payload


async def agent_workflows_payload() -> dict[str, Any]:
    global _agent_workflows_cache

    if _agent_workflows_cache is not None:
        return _payload_snapshot(_agent_workflows_cache)

    async with _agent_workflows_lock:
        if _agent_workflows_cache is not None:
            return _payload_snapshot(_agent_workflows_cache)

        cache_version = _agent_workflows_cache_version
        workflows = list_registered_workflows()
        for workflow_name in (REACT_AGENT_GRAPH_TYPE, ROUTER_GRAPH_TYPE):
            if workflow_name not in workflows:
                workflows.append(workflow_name)
        payload = {"workflows": workflows}
        if cache_version == _agent_workflows_cache_version:
            _agent_workflows_cache = payload
        return payload


async def agent_tools_payload() -> dict[str, Any]:
    global _agent_tools_cache

    if _agent_tools_cache is not None:
        return _payload_snapshot(_agent_tools_cache)

    async with _agent_tools_lock:
        if _agent_tools_cache is not None:
            return _payload_snapshot(_agent_tools_cache)

        cache_version = _agent_tools_cache_version
        started = time.perf_counter()
        cards = await agent_cards_payload()
        builtin_descriptors = find_descriptors(source=ToolSource.BUILTIN)
        tool_categories = {desc.name: desc.category.value for desc in builtin_descriptors}
        tool_config_schemas = serialize_tool_config_schemas(builtin_descriptors)
        tool_names = set(tool_categories)
        for card in cards["cards"]:
            if not card.get("valid"):
                continue
            tool_names.update(
                name
                for name in card.get("tools", [])
                if not str(name).startswith("mcp__")
            )

        payload = {
            "tools": sorted(tool_names),
            "tool_groups": _build_tool_groups(tool_names, tool_categories),
            "tool_config_schemas": tool_config_schemas,
        }
        if cache_version == _agent_tools_cache_version:
            _agent_tools_cache = payload
        logger.debug(
            "Built agent tools payload in %.1fms",
            (time.perf_counter() - started) * 1000,
        )
        return payload


async def agent_mcp_payload() -> dict[str, Any]:
    global _agent_mcp_cache

    if _agent_mcp_cache is not None:
        return _payload_snapshot(_agent_mcp_cache)

    async with _agent_mcp_lock:
        if _agent_mcp_cache is not None:
            return _payload_snapshot(_agent_mcp_cache)

        cache_version = _agent_mcp_cache_version
        started = time.perf_counter()
        cards = await agent_cards_payload()
        try:
            from agent.modules.mcp import list_all_agent_mcp_installs, list_mcp_installs

            mcp_installs = list_mcp_installs()
            mcp_servers = [str(item.get("server_name") or "") for item in mcp_installs]
            all_agent_installs = list_all_agent_mcp_installs()
            agent_mcp_installs = {
                str(card.get("name") or ""): all_agent_installs.get(str(card.get("name") or ""), [])
                for card in cards["cards"]
                if card.get("valid") and card.get("name")
            }
        except Exception:
            mcp_servers = []
            agent_mcp_installs = {}

        payload = {
            "mcp_server_options": mcp_servers,
            "mcp_installs": agent_mcp_installs,
        }
        if cache_version == _agent_mcp_cache_version:
            _agent_mcp_cache = payload
        logger.debug(
            "Built agent MCP payload in %.1fms",
            (time.perf_counter() - started) * 1000,
        )
        return payload


async def agent_provider_options_payload() -> dict[str, Any]:
    global _agent_provider_options_cache

    if _agent_provider_options_cache is not None:
        return _payload_snapshot(_agent_provider_options_cache)

    async with _agent_provider_options_lock:
        if _agent_provider_options_cache is not None:
            return _payload_snapshot(_agent_provider_options_cache)

        cache_version = _agent_provider_options_cache_version
        started = time.perf_counter()
        payload = await provider_model_options()
        if cache_version == _agent_provider_options_cache_version:
            _agent_provider_options_cache = payload
        logger.debug(
            "Built agent provider options payload in %.1fms",
            (time.perf_counter() - started) * 1000,
        )
        return payload


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
        reasoning_effort=body.reasoning_effort,
        tools=list(body.tools),
        tool_permissions=getattr(body, "tool_permissions", None),
        tool_configs={
            name: dict(values)
            for name, values in body.tool_configs.items()
            if name in body.tools and isinstance(values, dict)
        },
        mcp_servers=(
            list(body.mcp_servers)
            if hasattr(body, "mcp_servers") and body.mcp_servers is not None
            else None
        ),
        sub_agents=list(body.sub_agents) if body.sub_agents is not None else None,
        plan_approval_targets=list(body.plan_approval_targets),
        hidden=body.hidden,
        max_context_tokens=body.max_context_tokens,
        system_prompt=body.system_prompt.strip(),
    )
