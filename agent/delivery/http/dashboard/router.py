from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from apscheduler.job import Job
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from agent.delivery.http.dashboard.spa import spa_index_response
from agent.modules.admin_auth import get_current_admin
from agent.modules.agent_runtime import (
    NotifyChannel,
    get_active_session_registry,
    get_background_task_manager,
)
from agent.modules.agents import AgentCard, AgentConfig, get_catalog_service
from agent.modules.channels import (
    ChannelManager,
    get_channel_status,
    list_channel_statuses,
    start_all_channels,
    start_channel,
    stop_all_channels,
    stop_channel,
)
from agent.modules.github import get_github_automation_service, get_github_settings
from agent.modules.providers import (
    list_provider_model_catalog,
    list_provider_model_catalogs,
)
from agent.modules.scheduler import (
    TriggerType,
    execute_scheduled_task,
    get_scheduler,
    normalize_trigger,
)
from agent.modules.tools import get_default_tool_names
from agent.modules.users import get_pairing_service
from agent.modules.workspaces import (
    ensure_workspace_directory,
    get_thread_workspace_ref,
    get_workspace_backend,
    list_workspace_directories,
    resolve_workspace_ref,
    WorkspaceRef,
)
from agent.modules.workflows import (
    REACT_AGENT_GRAPH_TYPE,
    ROUTER_GRAPH_TYPE,
    list_registered_workflows,
)
from agent.shared.config import (
    PROVIDER_SETTING_FIELD_ORDER,
    ConfigService,
    get_setting_metadata,
    is_runtime_key,
    parse_provider_key,
)

router = APIRouter(tags=["dashboard"], dependencies=[Depends(get_current_admin)])
logger = logging.getLogger(__name__)
BACKGROUND_TASK_ACTIVE_STATUSES = {"pending", "running"}
SSE_HEARTBEAT_SECONDS = 15.0


# --- helpers ----------------------------------------------------------


def _get_channel_manager(request: Request) -> ChannelManager:
    channel_manager = getattr(request.app.state, "channel_manager", None)
    if channel_manager is None:
        raise HTTPException(status_code=503, detail="Channel manager is not available.")
    return channel_manager


def _get_config_service(request: Request) -> ConfigService:
    service = getattr(request.app.state, "config_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Config service is not available.")
    return service


def _get_scheduler() -> Any:
    try:
        return get_scheduler()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _group_settings_by_category(
    settings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for key, info in settings.items():
        category = info.get("category", "general")
        grouped.setdefault(category, {})[key] = info
    return grouped


def _is_provider_setting_key(key: str) -> bool:
    if key.startswith("llm.providers."):
        return True
    return key.startswith("llm.")


def _filter_settings[T](
    settings: dict[str, T],
    *,
    include_provider_settings: bool,
) -> dict[str, T]:
    return {
        key: value
        for key, value in settings.items()
        if _is_provider_setting_key(key) == include_provider_settings
    }


def _split_provider_settings(
    settings: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    global_settings: dict[str, dict[str, Any]] = {}
    provider_settings: dict[str, dict[str, Any]] = {}
    for key, info in settings.items():
        if key.startswith("llm.providers."):
            provider_settings[key] = info
            continue
        global_settings[key] = info
    return global_settings, provider_settings


def _build_provider_rows(
    settings: dict[str, dict[str, Any]],
    expected_fields: list[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for key, info in settings.items():
        parsed = parse_provider_key(key)
        if parsed is None:
            continue

        provider_name, field_name = parsed
        grouped.setdefault(provider_name, {})[field_name] = {
            "key": key,
            "info": info,
        }

    provider_rows: list[dict[str, Any]] = []
    for provider_name in sorted(grouped):
        fields = grouped[provider_name]

        if "provider" not in fields and "type" in fields:
            fields["provider"] = fields["type"]

        for field_name in expected_fields:
            if field_name in fields:
                continue

            synthetic_key = f"llm.providers.{provider_name}.{field_name}"
            metadata = get_setting_metadata(synthetic_key)
            synthetic_info: dict[str, Any] = {
                "value": None,
                "source": "default",
                "input_type": metadata["type"],
                "description": metadata["description"],
                "category": metadata["category"],
                "label": metadata["label"],
            }
            for optional_key in ("min", "max", "step"):
                if optional_key in metadata:
                    synthetic_info[optional_key] = metadata[optional_key]

            fields[field_name] = {
                "key": synthetic_key,
                "info": synthetic_info,
            }

        provider_rows.append({
            "name": provider_name,
            "fields": fields,
        })

    return provider_rows


def _ensure_runtime_keys(keys: list[str]) -> None:
    if invalid_keys := sorted(k for k in keys if not is_runtime_key(k)):
        suffix = "" if len(invalid_keys) == 1 else "s"
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported runtime setting{suffix}: {', '.join(invalid_keys)}.",
        )


def _dump_model(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _normalize_setting_value(key: str, value: Any | None) -> Any | None:
    if value is None or not key.endswith(".models"):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return value


def _normalize_setting_updates(values: dict[str, Any | None]) -> dict[str, Any | None]:
    return {key: _normalize_setting_value(key, value) for key, value in values.items()}


def _serialize_agent_card(card: AgentCard) -> dict[str, Any]:
    return _dump_model(card)


def _serialize_agent_config(config: AgentConfig) -> dict[str, Any]:
    return _dump_model(config)


def _serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _serialize_identity(identity: Any) -> dict[str, Any]:
    return {
        "id": getattr(identity, "id", None),
        "user_id": getattr(identity, "user_id", None),
        "platform": getattr(identity, "platform", ""),
        "external_id": getattr(identity, "external_id", ""),
        "created_at": _serialize_datetime(getattr(identity, "created_at", None)),
        "updated_at": _serialize_datetime(getattr(identity, "updated_at", None)),
    }


def _serialize_model_catalog(catalog: Any) -> dict[str, Any]:
    return {
        "provider": catalog.provider,
        "provider_type": catalog.provider_type,
        "default_model": catalog.default_model,
        "can_list_models": catalog.can_list_models,
        "models": [
            {
                "id": option.id,
                "label": option.label,
                "source": option.source,
            }
            for option in catalog.models
        ],
        "error": catalog.error,
    }


async def _provider_model_options() -> dict[str, Any]:
    try:
        catalogs = await list_provider_model_catalogs()
        default_catalog = await list_provider_model_catalog()
    except Exception as exc:
        logger.warning("Failed to load provider model options: %s", exc)
        return {
            "provider_names": [],
            "default_provider": "",
            "model_catalogs": [],
            "model_catalog_error": str(exc),
        }

    return {
        "provider_names": sorted(catalog.provider for catalog in catalogs),
        "default_provider": default_catalog.provider,
        "model_catalogs": [_serialize_model_catalog(catalog) for catalog in catalogs],
        "model_catalog_error": "",
    }


async def _agent_card_options(cards: list[AgentCard] | None = None) -> dict[str, Any]:
    catalog = get_catalog_service()
    cards = cards if cards is not None else catalog.list_agent_cards()

    workflows = list_registered_workflows()
    for workflow_name in (REACT_AGENT_GRAPH_TYPE, ROUTER_GRAPH_TYPE):
        if workflow_name not in workflows:
            workflows.append(workflow_name)

    tool_names = set(get_default_tool_names())
    agent_names = []
    for card in cards:
        if not card.valid:
            continue
        agent_names.append(card.name)
        tool_names.update(card.tools)

    return {
        "cards": [_serialize_agent_card(card) for card in cards],
        "tools": sorted(tool_names),
        "workflows": workflows,
        "agent_names": sorted(agent_names),
        **await _provider_model_options(),
    }


async def _paired_identities() -> list[dict[str, Any]]:
    pairing_service = get_pairing_service()
    identities = await pairing_service.list_paired_identities()
    return [_serialize_identity(identity) for identity in identities]


def _settings_payload(request: Request, *, include_provider_settings: bool) -> dict[str, Any]:
    service = _get_config_service(request)
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()
    settings = _filter_settings(
        settings_raw,
        include_provider_settings=include_provider_settings,
    )
    settings_sources = _filter_settings(
        settings_sources_raw,
        include_provider_settings=include_provider_settings,
    )

    if not include_provider_settings:
        return {
            "active_nav": "config",
            "page_title": "Runtime Configuration",
            "page_subtitle": "Manage channels, database, and security runtime settings.",
            "settings": settings,
            "by_category": _group_settings_by_category(settings),
            "settings_sources": settings_sources,
        }

    global_settings, provider_settings = _split_provider_settings(settings)
    provider_rows = _build_provider_rows(provider_settings, PROVIDER_SETTING_FIELD_ORDER)
    return {
        "active_nav": "providers",
        "page_title": "Provider Configuration",
        "page_subtitle": "Manage default provider and per-provider LLM credentials/models.",
        "settings": settings,
        "by_category": _group_settings_by_category(global_settings),
        "settings_sources": settings_sources,
        "provider_rows": provider_rows,
        "provider_name_options": [row["name"] for row in provider_rows],
        "provider_field_order": PROVIDER_SETTING_FIELD_ORDER,
    }


def _handle_agent_card_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected agent card operation failure.")
    return HTTPException(status_code=500, detail=str(exc))


def _agent_config_from_body(body: "AgentCardBody") -> AgentConfig:
    return AgentConfig(
        name=body.name.strip(),
        display_name=body.display_name.strip(),
        description=body.description.strip(),
        graph_type=body.graph_type.strip() or REACT_AGENT_GRAPH_TYPE,
        provider=body.provider.strip(),
        model=body.model.strip(),
        tools=list(body.tools),
        sub_agents=list(body.sub_agents) if body.sub_agents is not None else None,
        max_context_tokens=body.max_context_tokens,
        system_prompt=body.system_prompt.strip(),
    )


def _dashboard_spa() -> Response:
    return spa_index_response()


def _sse_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _active_session_for_thread(thread_id: str) -> dict[str, Any] | None:
    registry = get_active_session_registry()
    return next(
        (session for session in registry.list_active() if session["thread_id"] == thread_id),
        None,
    )


def _is_active_background_task(task: dict[str, Any] | None) -> bool:
    return bool(task and task.get("status") in BACKGROUND_TASK_ACTIVE_STATUSES)


async def _workspace_ref_for_thread(
    thread_id: str,
    *,
    include_default: bool = True,
) -> WorkspaceRef | None:
    if thread_id:
        task = get_background_task_manager().get_by_thread_id(thread_id)
        task_workspace = (task or {}).get("workspace")
        if task_workspace:
            return resolve_workspace_ref(task_workspace)

        try:
            stored_workspace = await get_thread_workspace_ref(thread_id)
        except Exception as exc:
            logger.debug(
                "Failed to load workspace for thread %s: %s",
                thread_id,
                exc,
            )
            stored_workspace = None
        if stored_workspace:
            return resolve_workspace_ref(stored_workspace)

    return resolve_workspace_ref(None) if include_default else None


async def _workspace_ref_from_request(
    *,
    thread_id: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    backend: str | None = None,
    locator: str | None = None,
) -> WorkspaceRef:
    if workspace is not None:
        return resolve_workspace_ref(workspace)
    if locator and locator.strip():
        return resolve_workspace_ref(
            {
                "backend": (backend or "local").strip() or "local",
                "locator": locator,
            }
        )
    if thread_id and thread_id.strip():
        stored = await _workspace_ref_for_thread(thread_id)
        if stored is not None:
            return stored
    return resolve_workspace_ref(None)


def _workspace_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (NotADirectoryError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected workspace operation failure.")
    return HTTPException(status_code=500, detail=str(exc))


# --- SPA routes -------------------------------------------------------


@router.get("/", include_in_schema=False)
@router.get("/chat", include_in_schema=False)
@router.get("/c/{thread_id:path}", include_in_schema=False)
@router.get("/sessions", include_in_schema=False)
@router.get("/tasks", include_in_schema=False)
@router.get("/scheduler", include_in_schema=False)
@router.get("/repositories", include_in_schema=False)
@router.get("/history", include_in_schema=False)
@router.get("/history/{thread_id:path}", include_in_schema=False)
@router.get("/settings/config", include_in_schema=False)
@router.get("/settings/providers", include_in_schema=False)
@router.get("/settings/channels", include_in_schema=False)
@router.get("/settings/agents", include_in_schema=False)
@router.get("/settings/security", include_in_schema=False)
@router.get("/settings/appearance", include_in_schema=False)
# Legacy routes — SPA handles redirect to /settings/*
@router.get("/channels", include_in_schema=False)
@router.get("/agents", include_in_schema=False)
@router.get("/change-password", include_in_schema=False)
async def dashboard_spa() -> Response:
    return _dashboard_spa()


# --- dashboard API ----------------------------------------------------


@router.get("/dashboard-api/session")
async def get_dashboard_session(current_admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    return {"authenticated": True, "admin_id": current_admin}


@router.get("/dashboard-api/overview")
async def get_dashboard_overview(request: Request) -> dict[str, Any]:
    channel_manager = _get_channel_manager(request)
    return {"services": list_channel_statuses(channel_manager)}


@router.get("/dashboard-api/channels")
async def get_dashboard_channels() -> dict[str, Any]:
    return {"identities": await _paired_identities()}


@router.get("/dashboard-api/agents")
async def get_dashboard_agents() -> dict[str, Any]:
    return await _agent_card_options()


@router.get("/dashboard-api/tasks")
async def get_dashboard_tasks() -> dict[str, Any]:
    manager = get_background_task_manager()
    catalog = get_catalog_service()
    return {
        "tasks": manager.list_all(),
        "agents": [_serialize_agent_config(agent) for agent in catalog.list_agents()],
        "identities": await _paired_identities(),
    }


@router.get("/dashboard-api/scheduler")
async def get_dashboard_scheduler() -> dict[str, Any]:
    try:
        scheduler = get_scheduler()
        jobs = _list_all_jobs()
        scheduler_timezone = _scheduler_timezone_label(scheduler)
    except RuntimeError:
        jobs = []
        scheduler_timezone = "local time"

    return {
        "jobs": jobs,
        "identities": await _paired_identities(),
        "scheduler_timezone": scheduler_timezone,
    }


@router.get("/dashboard-api/sessions")
async def get_dashboard_sessions() -> dict[str, Any]:
    registry = get_active_session_registry()
    sessions = registry.list_active()
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/dashboard-api/config")
async def get_dashboard_config(request: Request) -> dict[str, Any]:
    return _settings_payload(request, include_provider_settings=False)


@router.get("/dashboard-api/providers")
async def get_dashboard_providers(request: Request) -> dict[str, Any]:
    return _settings_payload(request, include_provider_settings=True)


@router.get("/dashboard-api/github")
async def get_dashboard_github(request: Request) -> dict[str, Any]:
    settings = get_github_settings()
    service = get_github_automation_service()
    cards = get_catalog_service().list_agent_cards()
    agent_names = sorted(card.name for card in cards if card.valid)
    webhook_url = f"{str(request.base_url).rstrip('/')}/channels/github/webhook"
    install_url = (
        f"https://github.com/apps/{settings.app_slug}/installations/new"
        if settings.app_slug
        else ""
    )
    return {
        "configured": settings.is_configured,
        "enabled": settings.enabled,
        "app_slug": settings.app_slug,
        "webhook_url": webhook_url,
        "install_url": install_url,
        "default_agent": settings.default_agent,
        "trigger_label": settings.trigger_label,
        "mention_triggers": list(settings.mention_triggers),
        "repositories": await service.list_repository_bindings(),
        "agent_names": agent_names,
    }


@router.get("/dashboard-api/workspace/default")
async def get_dashboard_default_workspace() -> dict[str, Any]:
    return {"workspace": resolve_workspace_ref(None).model_dump()}


@router.get("/dashboard-api/workspace/browse")
async def browse_dashboard_workspace(
    path: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return list_workspace_directories(path)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/tree")
async def get_dashboard_workspace_tree(
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
    path: str = Query(default=""),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
        )
        return get_workspace_backend(workspace).tree(path)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/changes")
async def get_dashboard_workspace_changes(
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
        )
        return get_workspace_backend(workspace).changes()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/diff")
async def get_dashboard_workspace_diff(
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
    path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
        )
        return get_workspace_backend(workspace).diff(path)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/file")
async def get_dashboard_workspace_file(
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
    path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
        )
        return get_workspace_backend(workspace).file(path)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


class WorkspaceRenameBody(BaseModel):
    thread_id: str | None = None
    workspace: WorkspaceRef | None = None
    path: str = Field(..., min_length=1)
    new_name: str = Field(..., min_length=1)


@router.post("/dashboard-api/workspace/rename")
async def rename_dashboard_workspace_entry(
    body: WorkspaceRenameBody,
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=body.thread_id,
            workspace=body.workspace,
        )
        return get_workspace_backend(workspace).rename(
            path=body.path,
            new_name=body.new_name,
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


class WorkspaceDeleteBody(BaseModel):
    thread_id: str | None = None
    workspace: WorkspaceRef | None = None
    path: str = Field(..., min_length=1)


class WorkspaceResolveBody(BaseModel):
    kind: str | None = None
    workspace: WorkspaceRef | None = None
    locator: str | None = None
    repository_id: int | None = None


@router.post("/dashboard-api/workspace/resolve")
async def resolve_dashboard_workspace(body: WorkspaceResolveBody) -> dict[str, Any]:
    kind_source = body.kind or (body.workspace.backend if body.workspace else "local")
    kind = kind_source.strip().lower()
    try:
        if kind == "local":
            ref = resolve_workspace_ref(
                body.workspace or {"backend": "local", "locator": body.locator}
            )
            root = ensure_workspace_directory(ref.locator)
            workspace = resolve_workspace_ref(
                {
                    "backend": "local",
                    "locator": str(root),
                    "label": str(root),
                    "metadata": ref.metadata,
                }
            )
            return {
                "kind": "local",
                "label": workspace.label,
                "workspace": workspace.model_dump(),
            }
        if kind == "github":
            if body.repository_id is None:
                raise ValueError("Repository ID is required.")
            return await get_github_automation_service().resolve_repository_workspace(
                body.repository_id,
            )
        raise ValueError(f"Unsupported workspace kind: {body.kind}")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.post("/dashboard-api/workspace/delete")
async def delete_dashboard_workspace_entry(
    body: WorkspaceDeleteBody,
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=body.thread_id,
            workspace=body.workspace,
        )
        return get_workspace_backend(workspace).delete(path=body.path)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.post("/dashboard-api/github/sync")
async def sync_dashboard_github() -> dict[str, Any]:
    service = get_github_automation_service()
    try:
        result = await service.sync_installations()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "synced", **result}


class GitHubRepositoryBindingBody(BaseModel):
    enabled: bool = False
    agent_name: str = ""
    trigger_label: str = ""
    mention_triggers: list[str] = Field(default_factory=list)
    notify_platform: str = ""
    notify_external_id: str = ""
    notify_channel_id: str = ""


@router.put("/dashboard-api/github/repositories/{repository_id}/binding")
async def update_dashboard_github_repository_binding(
    repository_id: int,
    body: GitHubRepositoryBindingBody,
) -> dict[str, Any]:
    service = get_github_automation_service()
    try:
        binding = await service.update_repository_binding(
            repository_id,
            enabled=body.enabled,
            agent_name=body.agent_name,
            trigger_label=body.trigger_label,
            mention_triggers=body.mention_triggers,
            notify_platform=body.notify_platform,
            notify_external_id=body.notify_external_id,
            notify_channel_id=body.notify_channel_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "updated", "repository": binding}


# --- agent card endpoints --------------------------------------------


class AgentCardBody(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str = REACT_AGENT_GRAPH_TYPE
    provider: str = "default"
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    sub_agents: list[str] | None = None
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


@router.get("/providers/models")
async def list_dashboard_provider_models(refresh: bool = False) -> dict[str, Any]:
    try:
        catalogs = await list_provider_model_catalogs(include_remote=refresh)
        default_catalog = await list_provider_model_catalog(include_remote=refresh)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "default_provider": default_catalog.provider,
        "providers": [_serialize_model_catalog(catalog) for catalog in catalogs],
    }


# --- channel endpoints ------------------------------------------------


@router.post("/channels/pair")
async def generate_pairing_code() -> dict[str, str]:
    pairing_service = get_pairing_service()
    code, user_id = await pairing_service.create_pairing_root_user_and_code()
    return {"code": code, "user_id": str(user_id)}


@router.delete("/channels/identities/{identity_id}")
async def unpair_identity(identity_id: int) -> dict[str, str]:
    pairing_service = get_pairing_service()
    await pairing_service.unpair_identity(identity_id)
    return {"status": "success"}


@router.get("/services")
async def get_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    return {"services": list_channel_statuses(channel_manager)}


@router.get("/services/{name}")
async def get_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        return get_channel_status(channel_manager, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/start")
async def start_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        status = await start_channel(channel_manager, name)
        return {"message": f"'{name}' is starting.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/stop")
async def stop_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        status = await stop_channel(channel_manager, name)
        return {"message": f"'{name}' stopped.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/start-all")
async def start_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    services = await start_all_channels(channel_manager)
    return {"services": services}


@router.post("/services/stop-all")
async def stop_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    services = await stop_all_channels(channel_manager)
    return {"services": services}


# --- settings endpoints -----------------------------------------------


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, dict[str, Any]]:
    service = _get_config_service(request)
    return {"settings": service.get_settings_overview()}


@router.get("/settings/sources")
async def get_settings_sources(request: Request) -> dict[str, dict[str, Any]]:
    service = _get_config_service(request)
    return {"sources": service.get_settings_sources()}


class UpdateSettingBody(BaseModel):
    value: Any | None


class UpdateSettingsBody(BaseModel):
    values: dict[str, Any | None]


@router.put("/settings/{key:path}")
async def update_setting(
    key: str,
    body: UpdateSettingBody,
    request: Request,
) -> dict[str, Any | None]:
    service = _get_config_service(request)
    _ensure_runtime_keys([key])

    value = _normalize_setting_value(key, body.value)
    service.update_setting(key, value)
    return {"status": "success", "key": key, "value": value}


@router.put("/settings")
async def update_settings(body: UpdateSettingsBody, request: Request) -> dict[str, Any]:
    if not body.values:
        return {"status": "success", "updated": []}

    _ensure_runtime_keys(list(body.values))

    values = _normalize_setting_updates(body.values)
    service = _get_config_service(request)
    for source in service._sources:
        if hasattr(source, "update_settings"):
            source.update_settings(values)
    service.reload()

    return {"status": "success", "updated": list(values.keys())}


# --- scheduler endpoints ----------------------------------------------


def _serialize_job(job: Job) -> dict[str, Any]:
    trigger_type = type(job.trigger).__name__.lower().replace("trigger", "")
    trigger_args: dict[str, Any] = {}
    trigger = job.trigger
    for attr in ["run_date", "weeks", "days", "hours", "minutes", "seconds",
                 "minute", "hour", "day", "month", "day_of_week"]:
        if hasattr(trigger, attr):
            val = getattr(trigger, attr)
            if val is not None:
                trigger_args[attr] = str(val) if not isinstance(val, (int, float, str)) else val
    if trigger_type == "date" and hasattr(trigger, "run_date"):
        from datetime import datetime
        run_date = getattr(trigger, "run_date")
        if isinstance(run_date, datetime):
            trigger_args["run_date"] = run_date.strftime("%Y-%m-%dT%H:%M")
    return {
        "id": job.id,
        "task": job.kwargs.get("task", "Unknown"),
        "platform": job.kwargs.get("platform", "-"),
        "user_id": job.kwargs.get("user_id", "-"),
        "trigger_type": trigger_type,
        "trigger_args": trigger_args,
        "next_run_time": (
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
            if job.next_run_time
            else None
        ),
        "paused": job.next_run_time is None,
    }


def _list_all_jobs() -> list[dict[str, Any]]:
    scheduler = get_scheduler()
    return [_serialize_job(j) for j in scheduler.get_jobs()]


def _scheduler_timezone_label(scheduler: Any) -> str:
    timezone = getattr(scheduler, "timezone", None)
    if timezone is None:
        return "local time"
    return getattr(timezone, "key", None) or str(timezone)


def _get_job_or_404(job_id: str) -> Job:
    scheduler = _get_scheduler()
    job = scheduler.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@router.get("/scheduler/jobs")
async def list_scheduler_jobs() -> dict[str, list[dict[str, Any]]]:
    try:
        jobs = _list_all_jobs()
    except RuntimeError:
        jobs = []
    return {"jobs": jobs}


class CreateJobBody(BaseModel):
    task: str
    platform: str
    user_id: str
    trigger_type: TriggerType
    trigger_args: dict[str, Any]


class UpdateJobBody(BaseModel):
    task: str | None = None
    platform: str | None = None
    user_id: str | None = None
    trigger_type: TriggerType | None = None
    trigger_args: dict[str, Any] | None = None


@router.post("/scheduler/jobs")
async def create_scheduler_job(body: CreateJobBody) -> dict[str, Any]:
    scheduler = _get_scheduler()

    try:
        trigger_type, trigger_args = normalize_trigger(
            body.trigger_type,
            body.trigger_args,
            scheduler,
        )
        job = scheduler.add_job(
            execute_scheduled_task,
            trigger=trigger_type,
            kwargs={"platform": body.platform, "user_id": body.user_id, "task": body.task},
            **trigger_args,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create job: {exc}") from exc

    return {"status": "created", "job": _serialize_job(job)}


@router.put("/scheduler/jobs/{job_id}")
async def update_scheduler_job(job_id: str, body: UpdateJobBody) -> dict[str, Any]:
    job = _get_job_or_404(job_id)

    if body.task is None and body.platform is None and body.user_id is None and body.trigger_type is None:
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        if body.task is not None or body.platform is not None or body.user_id is not None:
            new_kwargs = dict(job.kwargs)
            if body.task is not None:
                new_kwargs["task"] = body.task
            if body.platform is not None:
                new_kwargs["platform"] = body.platform
            if body.user_id is not None:
                new_kwargs["user_id"] = body.user_id
            job.modify(kwargs=new_kwargs)

        if body.trigger_type is not None and body.trigger_args is not None:
            scheduler = _get_scheduler()
            trigger_type, trigger_args = normalize_trigger(
                body.trigger_type,
                body.trigger_args,
                scheduler,
            )
            job.reschedule(trigger_type, **trigger_args)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to update job: {exc}") from exc

    return {"status": "updated", "job": _serialize_job(job)}


@router.delete("/scheduler/jobs/{job_id}")
async def delete_scheduler_job(job_id: str) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    job.remove()
    return {"status": "deleted", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/pause")
async def pause_scheduler_job(job_id: str) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    job.pause()
    return {"status": "paused", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/resume")
async def resume_scheduler_job(job_id: str) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    job.resume()
    return {"status": "resumed", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/run")
async def run_scheduler_job_now(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    platform = job.kwargs.get("platform")
    user_id = job.kwargs.get("user_id")
    task = job.kwargs.get("task")

    if not platform or not user_id or not task:
        raise HTTPException(
            status_code=400,
            detail=f"Job '{job_id}' is missing platform, user_id, or task.",
        )

    background_tasks.add_task(
        execute_scheduled_task,
        platform=platform,
        user_id=user_id,
        task=task,
    )
    return {"status": "queued", "job_id": job_id}


# --- active sessions endpoints ----------------------------------------


@router.get("/sessions/active")
async def list_active_sessions() -> dict[str, Any]:
    registry = get_active_session_registry()
    sessions = registry.list_active()
    return {"sessions": sessions, "count": len(sessions)}


# --- background tasks endpoints ---------------------------------------


class SubmitTaskBody(BaseModel):
    request: str
    agent_name: str = "default"
    workspace: WorkspaceRef | None = None
    notify_platform: str | None = None
    notify_external_id: str | None = None
    notify_channel_id: str | None = None


@router.get("/tasks/list")
async def list_background_tasks() -> dict[str, Any]:
    manager = get_background_task_manager()
    tasks = manager.list_all()
    return {"tasks": tasks}


@router.post("/tasks")
async def submit_background_task(body: SubmitTaskBody) -> dict[str, Any]:
    if not body.request.strip():
        raise HTTPException(status_code=400, detail="Request cannot be empty.")

    notify_channel = None
    if body.notify_platform and body.notify_external_id:
        notify_channel = NotifyChannel(
            platform=body.notify_platform,
            external_id=body.notify_external_id,
            channel_id=body.notify_channel_id or body.notify_external_id,
        )

    manager = get_background_task_manager()
    task_id = await manager.submit(
        request=body.request.strip(),
        agent_name=body.agent_name,
        workspace=body.workspace or resolve_workspace_ref(None),
        notify_channel=notify_channel,
    )
    return {"status": "submitted", "task_id": task_id}


@router.post("/tasks/{task_id}/cancel")
async def cancel_background_task(task_id: str) -> dict[str, str]:
    manager = get_background_task_manager()
    result = manager.cancel(task_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    if result == "not_running":
        raise HTTPException(status_code=400, detail="Task is not running.")
    return {"status": "cancelled", "task_id": task_id}


@router.delete("/tasks/{task_id}")
async def remove_background_task(task_id: str) -> dict[str, str]:
    manager = get_background_task_manager()
    removed = await manager.remove(task_id)
    if not removed:
        raise HTTPException(
            status_code=400,
            detail=f"Task '{task_id}' not found or still running.",
        )
    return {"status": "removed", "task_id": task_id}


# --- chat history endpoints -------------------------------------------


class RenameThreadBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)


def _get_checkpointer():
    from agent.modules.workflows import get_checkpointer

    try:
        return get_checkpointer()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parse_thread_id_safe(thread_id: str) -> dict[str, str]:
    from agent.modules.conversations import parse_thread_metadata

    return parse_thread_metadata(thread_id)


async def _get_checkpoint_stats(thread_id: str) -> dict[str, Any]:
    """Read lightweight checkpoint stats via the checkpointer API."""
    try:
        checkpointer = _get_checkpointer()
    except HTTPException:
        return {"latest_checkpoint_id": "", "checkpoint_count": 0}

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    latest_checkpoint_id = ""
    checkpoint_count = 0

    try:
        async for checkpoint_tuple in checkpointer.alist(config):
            checkpoint_count += 1
            if latest_checkpoint_id:
                continue

            tuple_config = getattr(checkpoint_tuple, "config", {}) or {}
            configurable = tuple_config.get("configurable", {})
            if isinstance(configurable, dict):
                latest_checkpoint_id = str(configurable.get("checkpoint_id", "") or "")

            if not latest_checkpoint_id:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
                if isinstance(checkpoint, dict):
                    latest_checkpoint_id = str(checkpoint.get("id", "") or "")
    except Exception as exc:
        logger.warning("Failed to get checkpoint stats for %s: %s", thread_id, exc)
        return {"latest_checkpoint_id": "", "checkpoint_count": 0}

    return {
        "latest_checkpoint_id": latest_checkpoint_id,
        "checkpoint_count": checkpoint_count,
    }


async def _list_legacy_threads_from_checkpoints(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fallback for checkpoint-only threads created before thread metadata existed."""
    from agent.modules.conversations import THREAD_KIND_USER, infer_thread_kind

    try:
        checkpointer = _get_checkpointer()
    except HTTPException:
        return []

    summaries: dict[str, dict[str, Any]] = {}
    try:
        async for checkpoint_tuple in checkpointer.alist(None):
            tuple_config = getattr(checkpoint_tuple, "config", {}) or {}
            configurable = tuple_config.get("configurable", {})
            if not isinstance(configurable, dict):
                continue

            thread_id = str(configurable.get("thread_id", "") or "")
            if not thread_id or infer_thread_kind(thread_id) != THREAD_KIND_USER:
                continue

            checkpoint_ns = str(configurable.get("checkpoint_ns", "") or "")
            if checkpoint_ns:
                continue

            checkpoint_id = str(configurable.get("checkpoint_id", "") or "")
            summary = summaries.get(thread_id)
            if summary is None:
                summary = {
                    "thread_id": thread_id,
                    "latest_checkpoint_id": checkpoint_id,
                    "checkpoint_count": 0,
                    "agent_name": "",
                    "title": thread_id,
                    "kind": THREAD_KIND_USER,
                    "created_at": None,
                    "updated_at": None,
                    **_parse_thread_id_safe(thread_id),
                }
                summaries[thread_id] = summary
            summary["checkpoint_count"] += 1
    except Exception as exc:
        logger.warning("Failed to list legacy checkpoint threads: %s", exc)
        return []

    rows = list(summaries.values())
    if offset:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


async def _list_threads_from_db(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List user-facing conversation threads from the domain table."""
    from agent.modules.conversations import list_conversation_threads

    try:
        threads = await list_conversation_threads(limit=limit, offset=offset)
    except Exception as exc:
        logger.warning("Failed to list conversation threads: %s", exc)
        return []

    if not threads:
        return await _list_legacy_threads_from_checkpoints(limit=limit, offset=offset)

    result = []
    for thread in threads:
        stats = await _get_checkpoint_stats(thread["thread_id"])
        result.append({
            **thread,
            **stats,
        })
    return result


def _serialize_message_attachments(msg: Any) -> list[dict[str, Any]]:
    additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
    raw_attachments = additional_kwargs.get("attachments")
    if not isinstance(raw_attachments, list):
        return []

    attachments = []
    for attachment in raw_attachments:
        if not isinstance(attachment, dict):
            continue
        attachments.append(
            {
                "name": str(attachment.get("name") or ""),
                "mime_type": str(attachment.get("mime_type") or ""),
                "size": int(attachment.get("size") or 0),
                "kind": str(attachment.get("kind") or ""),
            }
        )
    return attachments


def _human_content_text(content: Any, *, has_attachments: bool) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    text_parts: list[str] = []
    for index, part in enumerate(content):
        if isinstance(part, dict):
            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text":
                if has_attachments and index > 0:
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
                continue
            if part_type in {"image", "file", "audio", "video"} and not has_attachments:
                text_parts.append(f"[Attached {part_type}]")
            continue
        text_parts.append(str(part))

    return "\n\n".join(text_parts).strip()


async def _get_thread_messages(thread_id: str) -> list[dict[str, Any]]:
    """Get messages from a thread via the checkpointer."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    checkpointer = _get_checkpointer()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
    except Exception as exc:
        logger.warning("Failed to get checkpoint for thread %s: %s", thread_id, exc)
        return []

    if checkpoint_tuple is None:
        return []

    checkpoint_data = checkpoint_tuple.checkpoint
    channel_values = checkpoint_data.get("channel_values", {})
    messages = channel_values.get("messages", [])

    result = []
    for msg in messages:
        entry: dict[str, Any] = {"id": getattr(msg, "id", None)}
        if isinstance(msg, HumanMessage):
            attachments = _serialize_message_attachments(msg)
            entry["role"] = "user"
            entry["content"] = _human_content_text(
                msg.content,
                has_attachments=bool(attachments),
            )
            if attachments:
                entry["attachments"] = attachments
        elif isinstance(msg, AIMessage):
            from agent.shared.infrastructure.parsing import extract_final_text_content

            content = extract_final_text_content(getattr(msg, "content", None))
            entry["role"] = "assistant"
            entry["content"] = content or ""
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "args": tc.get("args"),
                    }
                    for tc in tool_calls
                ]
        elif isinstance(msg, ToolMessage):
            from agent.shared.infrastructure.parsing import extract_final_text_content

            entry["role"] = "tool"
            entry["name"] = getattr(msg, "name", None)
            entry["tool_call_id"] = getattr(msg, "tool_call_id", None)
            entry["content"] = extract_final_text_content(
                getattr(msg, "content", None)
            ) or ""
        else:
            entry["role"] = "system"
            entry["content"] = str(getattr(msg, "content", ""))

        result.append(entry)

    return result


@router.get("/dashboard-api/chat-history")
async def get_chat_history(
    limit: int | None = Query(default=None, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    fetch_limit = limit + 1 if limit is not None else None
    threads = await _list_threads_from_db(limit=fetch_limit, offset=offset)
    has_more = limit is not None and len(threads) > limit

    if limit is not None:
        threads = threads[:limit]

    return {
        "threads": threads,
        "has_more": has_more,
        "next_offset": offset + len(threads),
    }


@router.get("/dashboard-api/chat-history/{thread_id:path}")
async def get_chat_thread_messages(thread_id: str) -> dict[str, Any]:
    messages = await _get_thread_messages(thread_id)
    from agent.modules.conversations import get_conversation_thread

    metadata = await get_conversation_thread(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    workspace = await _workspace_ref_for_thread(thread_id, include_default=False)
    return {
        "thread_id": thread_id,
        "messages": messages,
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


@router.patch("/dashboard-api/chat-history/{thread_id:path}")
async def rename_chat_thread(
    thread_id: str,
    body: RenameThreadBody,
) -> dict[str, Any]:
    from agent.modules.conversations import rename_conversation_thread

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Thread title cannot be empty.")

    metadata = await rename_conversation_thread(thread_id, title)
    stats = await _get_checkpoint_stats(thread_id)
    return {
        **metadata,
        **stats,
    }


@router.delete("/dashboard-api/chat-history/{thread_id:path}")
async def delete_chat_thread(thread_id: str) -> dict[str, str]:
    from agent.modules.conversations import mark_conversation_thread_deleted
    from agent.modules.workflows import delete_workflow_thread_tree

    await mark_conversation_thread_deleted(thread_id)
    await delete_workflow_thread_tree(thread_id)
    return {"status": "deleted", "thread_id": thread_id}


# --- background task conversation endpoints -------------------------------------------


async def _list_background_threads_from_db(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List background task threads from the domain table."""
    from agent.modules.conversations import THREAD_KIND_BACKGROUND, list_conversation_threads

    try:
        threads = await list_conversation_threads(
            limit=limit,
            offset=offset,
            kind=THREAD_KIND_BACKGROUND,
        )
    except Exception as exc:
        logger.warning("Failed to list background task threads: %s", exc)
        return []

    if not threads:
        return []

    result = []
    for thread in threads:
        stats = await _get_checkpoint_stats(thread["thread_id"])
        result.append({
            **thread,
            **stats,
        })
    return result


@router.get("/dashboard-api/background-tasks")
async def list_background_task_threads(
    limit: int | None = Query(default=None, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    fetch_limit = limit + 1 if limit is not None else None
    threads = await _list_background_threads_from_db(limit=fetch_limit, offset=offset)
    has_more = limit is not None and len(threads) > limit

    if limit is not None:
        threads = threads[:limit]

    return {
        "tasks": threads,
        "has_more": has_more,
        "next_offset": offset + len(threads),
    }


@router.get("/dashboard-api/background-tasks/{thread_id:path}")
async def get_background_task_messages(thread_id: str) -> dict[str, Any]:
    messages = await _get_thread_messages(thread_id)
    from agent.modules.conversations import get_conversation_thread

    metadata = await get_conversation_thread(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    workspace = await _workspace_ref_for_thread(thread_id, include_default=False)
    return {
        "thread_id": thread_id,
        "messages": messages,
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


async def _get_background_task_stream_metadata(
    thread_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    from agent.modules.conversations import THREAD_KIND_BACKGROUND, get_conversation_thread

    manager = get_background_task_manager()
    task = manager.get_by_thread_id(thread_id)
    metadata = await get_conversation_thread(thread_id)
    if task is None and (
        metadata is None or metadata.get("kind") != THREAD_KIND_BACKGROUND
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Background task thread '{thread_id}' not found.",
        )
    return task, metadata


async def _get_thread_messages_for_stream(thread_id: str) -> list[dict[str, Any]]:
    try:
        return await _get_thread_messages(thread_id)
    except HTTPException as exc:
        if exc.status_code == 503:
            return []
        raise


async def _background_task_snapshot(
    thread_id: str,
    *,
    task: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from agent.modules.conversations import THREAD_KIND_BACKGROUND

    manager = get_background_task_manager()
    current_task = task if task is not None else manager.get_by_thread_id(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    if current_task is not None:
        parsed = {**parsed, "kind": THREAD_KIND_BACKGROUND}

    workspace = await _workspace_ref_for_thread(thread_id)
    return {
        "thread_id": thread_id,
        "messages": await _get_thread_messages_for_stream(thread_id),
        "task": current_task,
        "active_session": _active_session_for_thread(thread_id),
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


@router.get("/dashboard-api/background-task-events")
async def stream_background_task_events(
    thread_id: str = Query(..., min_length=1),
) -> StreamingResponse:
    manager = get_background_task_manager()
    task, metadata = await _get_background_task_stream_metadata(thread_id)
    queue = manager.subscribe(thread_id)
    try:
        initial_snapshot = await _background_task_snapshot(
            thread_id,
            task=task,
            metadata=metadata,
        )
    except Exception:
        manager.unsubscribe(thread_id, queue)
        raise

    async def event_generator():
        try:
            yield _sse_event("snapshot", initial_snapshot)
            latest_task = initial_snapshot.get("task")
            if not _is_active_background_task(latest_task):
                yield _sse_event("done", {"task": latest_task})
                return

            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    latest_task = manager.get_by_thread_id(thread_id)
                    yield _sse_event("heartbeat", {})
                    if not _is_active_background_task(latest_task):
                        yield _sse_event("done", {"task": latest_task})
                        return
                    continue

                event_name = str(event.get("event") or "message")
                event_data = event.get("data")
                yield _sse_event(
                    event_name,
                    event_data if isinstance(event_data, dict) else {},
                )
                if event_name == "done":
                    return
        finally:
            manager.unsubscribe(thread_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/dashboard-api/background-tasks/{thread_id:path}")
async def delete_background_task_thread(thread_id: str) -> dict[str, str]:
    from agent.modules.conversations import mark_conversation_thread_deleted
    from agent.modules.agent_runtime import get_background_task_repository
    from agent.modules.workflows import delete_workflow_thread_tree

    await get_background_task_repository().mark_deleted_by_thread_id(thread_id)
    await mark_conversation_thread_deleted(thread_id)
    await delete_workflow_thread_tree(thread_id)
    return {"status": "deleted", "thread_id": thread_id}
