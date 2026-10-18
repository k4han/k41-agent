from __future__ import annotations

import asyncio
import logging
import threading
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
_MCP_TOOLS_LOAD_TIMEOUT_SECONDS = 5.0
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
_agent_cards_lock: asyncio.Lock | None = None
_agent_tools_lock: asyncio.Lock | None = None
_agent_workflows_lock: asyncio.Lock | None = None
_agent_mcp_lock: asyncio.Lock | None = None
_agent_provider_options_lock: asyncio.Lock | None = None
_locks_guard = threading.Lock()


def _get_lock(name: str) -> asyncio.Lock:
    """Lazily create cache locks inside a running loop."""
    global _agent_cards_lock, _agent_tools_lock, _agent_workflows_lock
    global _agent_mcp_lock, _agent_provider_options_lock
    current = globals().get(name)
    if current is None:
        with _locks_guard:
            current = globals().get(name)
            if current is None:
                current = asyncio.Lock()
                globals()[name] = current
    return current

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
    """Warm dashboard options without connecting to external MCP servers."""
    await asyncio.gather(
        agent_cards_payload(),
        agent_tools_payload(),
        agent_workflows_payload(),
        agent_provider_options_payload(),
    )


async def agent_cards_payload() -> dict[str, Any]:
    global _agent_cards_cache

    if _agent_cards_cache is not None:
        return _payload_snapshot(_agent_cards_cache)

    async with _get_lock("_agent_cards_lock"):
        if _agent_cards_cache is not None:
            return _payload_snapshot(_agent_cards_cache)

        cache_version = _agent_cards_cache_version
        started = time.perf_counter()
        cards = get_catalog_service().list_agent_cards()
        agent_names = sorted(card.name for card in cards if card.valid and not card.internal)
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

    async with _get_lock("_agent_workflows_lock"):
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

    async with _get_lock("_agent_tools_lock"):
        if _agent_tools_cache is not None:
            return _payload_snapshot(_agent_tools_cache)

        cache_version = _agent_tools_cache_version
        started = time.perf_counter()
        cards = await agent_cards_payload()
        builtin_descriptors = [
            descriptor for descriptor in find_descriptors(source=ToolSource.BUILTIN)
            if descriptor.name not in {"delegate_task", "call_agent"}
        ]
        tool_categories = {desc.name: desc.category.value for desc in builtin_descriptors}
        tool_config_schemas = serialize_tool_config_schemas(builtin_descriptors)
        tool_names = set(tool_categories)
        for card in cards["cards"]:
            if not card.get("valid"):
                continue
            tool_names.update(
                name
                for name in card.get("tools", [])
                if name not in {"delegate_task", "call_agent"} and not str(name).startswith("mcp__")
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

    async with _get_lock("_agent_mcp_lock"):
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
        if not mcp_servers:
            payload = {
                "mcp_server_options": [],
                "mcp_installs": agent_mcp_installs,
                "mcp_tools": {},
            }
            if cache_version == _agent_mcp_cache_version:
                _agent_mcp_cache = payload
            return payload
        from agent.bootstrap.container import require_active_container
        from agent.modules.tools import ensure_mcp_loaded

        active = require_active_container()
        load_task = active._mcp_load_task
        if load_task is None or load_task.done():
            load_task = asyncio.create_task(
                ensure_mcp_loaded(container=active), name="dashboard-mcp-tools",
            )
            active._mcp_load_task = load_task
        # Capture state needed after network I/O, then release the lock
        # so slow/offline MCP does not block other dashboard requests.
        snapshot = (cards, mcp_servers, agent_mcp_installs, cache_version, started)

    cards, mcp_servers, agent_mcp_installs, cache_version, started = snapshot
    try:
        async with asyncio.timeout(_MCP_TOOLS_LOAD_TIMEOUT_SECONDS):
            loaded = await asyncio.shield(load_task)
    except TimeoutError:
        logger.warning("MCP tool loading timed out; returning the available catalog")
        loaded = False

    async with _get_lock("_agent_mcp_lock"):
        if _agent_mcp_cache is not None:
            return _payload_snapshot(_agent_mcp_cache)
        payload = {
            "mcp_server_options": mcp_servers,
            "mcp_installs": agent_mcp_installs,
            "mcp_tools": {
                server: sorted(d.name for d in find_descriptors(source=ToolSource.MCP, tags=[server]))
                for server in mcp_servers
            },
        }
        if loaded and cache_version == _agent_mcp_cache_version:
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

    async with _get_lock("_agent_provider_options_lock"):
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
    from agent.modules.tools import TOOL_ALIASES, canonical_tool_names

    canonical_tools = canonical_tool_names(list(body.tools))
    allowed = set(canonical_tools)
    return AgentConfig(
        name=body.name.strip(),
        display_name=body.display_name.strip(),
        description=body.description.strip(),
        graph_type=body.graph_type.strip() or REACT_AGENT_GRAPH_TYPE,
        provider=body.provider.strip(),
        model=body.model.strip(),
        reasoning_effort=body.reasoning_effort,
        tools=canonical_tools,
        tool_permissions=getattr(body, "tool_permissions", None),
        tool_configs={
            TOOL_ALIASES.get(str(name), str(name)): dict(values)
            for name, values in body.tool_configs.items()
            if TOOL_ALIASES.get(str(name), str(name)) in allowed and isinstance(values, dict)
        },
        sub_agents=list(body.sub_agents) if body.sub_agents is not None else None,
        plan_approval_targets=list(body.plan_approval_targets),
        internal=body.internal,
        context_compact_threshold=body.context_compact_threshold,
        system_prompt=body.system_prompt.strip(),
    )
