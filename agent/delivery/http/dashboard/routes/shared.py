from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from apscheduler.job import Job
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from agent.delivery.http.dashboard.spa import spa_index_response
from agent.modules.agent_runtime import (
    get_active_session_registry,
    get_background_task_manager,
)
from agent.modules.agents import AgentCard, AgentConfig, get_catalog_service
from agent.modules.channels import ChannelManager
from agent.modules.providers import (
    list_provider_model_catalog,
    list_provider_model_catalogs,
)
from agent.modules.scheduler import get_scheduler
from agent.modules.tools import get_default_tool_names
from agent.modules.users import get_pairing_service
from agent.modules.workflows import (
    REACT_AGENT_GRAPH_TYPE,
    ROUTER_GRAPH_TYPE,
    list_registered_workflows,
)
from agent.modules.workspaces import (
    WorkspaceRef,
    get_thread_workspace_ref,
    resolve_workspace_ref,
)
from agent.shared.config import (
    PROVIDER_SETTING_FIELD_ORDER,
    ConfigService,
    get_setting_metadata,
    is_runtime_key,
    parse_provider_key,
)
from agent.shared.infrastructure.config_file import coerce_bool

if TYPE_CHECKING:
    from agent.delivery.http.dashboard.routes.agents import AgentCardBody


logger = logging.getLogger(__name__)
BACKGROUND_TASK_ACTIVE_STATUSES = {"pending", "running"}
SSE_HEARTBEAT_SECONDS = 15.0
NO_WORKSPACE_KEY = "no-workspace"
NO_WORKSPACE_LABEL = "No workspace"
PROVIDER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
PROVIDER_TYPE_OPTIONS: list[dict[str, Any]] = [
    {
        "value": "google",
        "label": "Google",
        "description": "Google Gemini provider",
        "requires_base_url": False,
    },
    {
        "value": "anthropic",
        "label": "Anthropic",
        "description": "Anthropic Claude provider",
        "requires_base_url": False,
    },
    {
        "value": "openai_compatible",
        "label": "OpenAI-compatible",
        "description": "Custom endpoint that implements the OpenAI chat API",
        "requires_base_url": True,
    },
]
SUPPORTED_PROVIDER_TYPES = {option["value"] for option in PROVIDER_TYPE_OPTIONS}


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


def _normalize_provider_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _provider_type_label(provider_type: str) -> str:
    for option in PROVIDER_TYPE_OPTIONS:
        if option["value"] == provider_type:
            return str(option["label"])
    return provider_type


def _provider_type_requires_base_url(provider_type: str) -> bool:
    for option in PROVIDER_TYPE_OPTIONS:
        if option["value"] == provider_type:
            return bool(option["requires_base_url"])
    return provider_type == "openai_compatible"


def _field_value(
    fields: dict[str, dict[str, Any]],
    field_name: str,
    default: Any = None,
) -> Any:
    entry = fields.get(field_name)
    if not entry:
        return default
    value = entry.get("info", {}).get("value", default)
    return default if value is None else value


def _field_text(fields: dict[str, dict[str, Any]], field_name: str) -> str:
    value = _field_value(fields, field_name, "")
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _provider_entries_from_flat_config(
    flat_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for key, value in flat_config.items():
        parsed = parse_provider_key(key)
        if parsed is None:
            continue

        provider_name, field_name = parsed
        normalized_name = _normalize_provider_name(provider_name)
        if not normalized_name:
            continue

        entry = providers.setdefault(
            normalized_name,
            {"name": provider_name},
        )
        canonical_field_name = "type" if field_name == "provider" else field_name
        if field_name == "provider" and "type" in entry:
            continue
        entry[canonical_field_name] = value

    return providers


def _provider_type_from_body(provider_type: str) -> str:
    normalized = provider_type.strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_PROVIDER_TYPES:
        supported = ", ".join(sorted(SUPPORTED_PROVIDER_TYPES))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported provider type: {provider_type!r}. Supported values: {supported}.",
        )
    return normalized


def _validate_provider_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Provider name is required.")
    if normalized.lower() == "default":
        raise HTTPException(
            status_code=400,
            detail="'default' is reserved and cannot be used as a provider name.",
        )
    if not PROVIDER_NAME_PATTERN.match(normalized):
        raise HTTPException(
            status_code=400,
            detail="Provider name can only contain letters, numbers, underscores, and hyphens.",
        )
    return normalized


def _provider_config_name(
    service: ConfigService,
    provider_name: str,
) -> str | None:
    normalized_name = _normalize_provider_name(provider_name)
    providers = _provider_entries_from_flat_config(service.get_all())
    entry = providers.get(normalized_name)
    if entry is None:
        return None
    return str(entry["name"])


def _update_config_settings(
    service: ConfigService,
    values: dict[str, Any | None],
    *,
    require_writable: bool = False,
) -> None:
    updated = False
    for source in service._sources:
        update_settings = getattr(source, "update_settings", None)
        if callable(update_settings):
            update_settings(values)
            updated = True
    if require_writable and not updated:
        raise HTTPException(status_code=503, detail="No writable config source is available.")
    service.reload()


def _delete_config_tree(service: ConfigService, key: str) -> bool:
    deleted = False
    for source in service._sources:
        delete_setting_tree = getattr(source, "delete_setting_tree", None)
        if callable(delete_setting_tree):
            deleted = bool(delete_setting_tree(key)) or deleted
    service.reload()
    return deleted


def _validate_default_provider_update(
    service: ConfigService,
    values: dict[str, Any | None],
) -> None:
    configured_default = str(service.get("llm.default_provider", "") or "").strip()
    default_provider = str(values.get("llm.default_provider", configured_default) or "").strip()
    if not default_provider:
        return

    should_validate = "llm.default_provider" in values
    if not should_validate and configured_default:
        default_prefix = f"llm.providers.{configured_default}."
        should_validate = any(key.startswith(default_prefix) for key in values)
    if not should_validate:
        return

    flat_config = service.get_all()
    flat_config.update(values)
    providers = _provider_entries_from_flat_config(flat_config)
    provider = providers.get(_normalize_provider_name(default_provider))
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown default provider: {default_provider}.",
        )

    if not coerce_bool(provider.get("enabled", True)):
        raise HTTPException(
            status_code=400,
            detail="Default provider must be enabled.",
        )
    if not str(provider.get("api_key") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Default provider must have an API key.",
        )
    if str(provider.get("type") or "").strip() == "openai_compatible" and not str(
        provider.get("base_url") or ""
    ).strip():
        raise HTTPException(
            status_code=400,
            detail="OpenAI-compatible default provider must have a base URL.",
        )
    if not str(provider.get("default_model") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Default provider must have a default model.",
        )


def _build_provider_rows(
    settings: dict[str, dict[str, Any]],
    expected_fields: list[str],
    default_provider: str,
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
    normalized_default_provider = _normalize_provider_name(default_provider)
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

        provider_type = _field_text(fields, "type") or _field_text(fields, "provider")
        enabled = coerce_bool(_field_value(fields, "enabled", True))
        default_model = _field_text(fields, "default_model")
        api_key_configured = bool(_field_text(fields, "api_key"))
        base_url_configured = bool(_field_text(fields, "base_url"))
        requires_base_url = _provider_type_requires_base_url(provider_type)
        is_default = (
            bool(normalized_default_provider)
            and _normalize_provider_name(provider_name) == normalized_default_provider
        )
        ready = (
            enabled
            and api_key_configured
            and bool(default_model)
            and (not requires_base_url or base_url_configured)
        )
        default_block_reason = ""
        if is_default:
            default_block_reason = "Provider is already default."
        elif not enabled:
            default_block_reason = "Enable the provider before making it default."
        elif not default_model:
            default_block_reason = "Set a default model before making this provider default."
        elif requires_base_url and not base_url_configured:
            default_block_reason = "Set a base URL before making this provider default."
        elif not api_key_configured:
            default_block_reason = "Set an API key before making this provider default."

        provider_rows.append({
            "name": provider_name,
            "fields": fields,
            "type": provider_type,
            "type_label": _provider_type_label(provider_type),
            "requires_base_url": requires_base_url,
            "enabled": enabled,
            "is_default": is_default,
            "ready": ready,
            "can_delete": not is_default,
            "delete_block_reason": "Default provider cannot be deleted." if is_default else "",
            "can_set_default": ready and not is_default,
            "default_block_reason": default_block_reason,
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

    try:
        from agent.modules.mcp import list_mcp_servers
        mcp_servers = [server.name for server in list_mcp_servers()]
    except Exception:
        mcp_servers = []

    return {
        "cards": [_serialize_agent_card(card) for card in cards],
        "tools": sorted(tool_names),
        "workflows": workflows,
        "agent_names": sorted(agent_names),
        "mcp_server_options": mcp_servers,
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
    default_provider = str(global_settings.get("llm.default_provider", {}).get("value") or "")
    provider_rows = _build_provider_rows(
        provider_settings,
        PROVIDER_SETTING_FIELD_ORDER,
        default_provider,
    )
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
        "provider_type_options": PROVIDER_TYPE_OPTIONS,
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


def _handle_prompt_variable_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected prompt variable operation failure.")
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
        mcp_servers=list(body.mcp_servers) if hasattr(body, "mcp_servers") and body.mcp_servers is not None else None,
        sub_agents=list(body.sub_agents) if body.sub_agents is not None else None,
        hidden=body.hidden,
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
