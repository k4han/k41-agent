"""Authenticated dashboard project catalog endpoints."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.modules.projects import ProjectWorkspaceError, add_project, get_project, list_projects, update_project
from agent.modules.projects import get_project_instructions, save_project_instructions
from agent.modules.workspaces import WorkspaceBinding, WorkspaceRef
from agent.modules.workspaces import MAX_FILE_BYTES
from agent.delivery.http.dashboard.routes.helpers.workspace import workspace_http_error

router = APIRouter()
logger = logging.getLogger(__name__)


class ProjectCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: WorkspaceRef | WorkspaceBinding
    label: str | None = Field(default=None, max_length=255)

    @field_validator("workspace")
    @classmethod
    def require_locator(cls, value):
        execution = value.execution if isinstance(value, WorkspaceBinding) else value
        if not execution.locator.strip():
            raise ValueError("Workspace locator is required.")
        return value

    @field_validator("label")
    @classmethod
    def clean_label(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Project name cannot be empty.")
        return value.strip() if value is not None else None


class ProjectUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="", min_length=1, max_length=255)
    is_pinned: bool = False
    status: Literal["active", "archived"] = "active"

    @field_validator("label")
    @classmethod
    def clean_label(cls, value):
        if not value.strip():
            raise ValueError("Project name cannot be empty.")
        return value.strip()


class ProjectInstructionsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=MAX_FILE_BYTES)
    expected_version: str | None = Field(..., pattern=r"^[a-f0-9]{64}$")

    @field_validator("content", mode="before")
    @classmethod
    def require_utf8(cls, value):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise HTTPException(status_code=400, detail="AGENTS.md must be valid UTF-8 text.") from exc
        return value


class ProjectRuntimeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_workspace_id: int | None = Field(default=None, ge=1)


class MigrationSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: int = Field(ge=1)


class ProjectRuntimeSwitchBody(ProjectRuntimeBody):
    backend: Literal["local", "daytona", "modal"]
    expected_generation: int = Field(ge=1)


@router.get("/dashboard-api/workspace/runtime/{thread_id:path}")
async def get_thread_runtime(thread_id: str):
    from agent.modules.projects import get_project_workspace_service
    service = get_project_workspace_service()
    if not service.container._persistence_ready:
        return {"workspace": None, "runtime": None}
    try:
        binding = await service.resolve_thread(thread_id)
        if binding is None:
            return {"workspace": None, "runtime": None}
        description = await service.describe(binding.project_id)
        workspace = next(item for item in description["workspaces"] if item["id"] == binding.project_workspace_id)
        return {"workspace": binding.model_dump(), "runtime": workspace["runtime"], "kind": workspace["kind"],
                "workspace_name": workspace["name"], "persistence": workspace["persistence"],
                "local_link": workspace["local_link"]}
    except Exception as exc:
        raise project_resource_error(exc) from exc


@router.post("/dashboard-api/projects/{project_id}/runtime/switch", status_code=202)
async def switch_project_runtime(body: ProjectRuntimeSwitchBody, project_id: int = Path(ge=1)):
    from agent.modules.projects import get_project_workspace_service
    try:
        return await get_project_workspace_service().switcher.request(
            project_id, body.backend, body.expected_generation, body.project_workspace_id,
        )
    except Exception as exc:
        raise project_resource_error(exc) from exc


@router.post("/dashboard-api/projects/{project_id}/runtime/ensure")
async def ensure_project_runtime(body: ProjectRuntimeBody, project_id: int = Path(ge=1)):
    from agent.modules.projects import get_project_workspace_service
    try:
        binding = await get_project_workspace_service().ensure(project_id, body.project_workspace_id)
        return {"workspace": binding.model_dump()}
    except Exception as exc:
        raise project_resource_error(exc) from exc


class ProjectRuntimeRecoveryBody(ProjectRuntimeBody):
    action: Literal["restore", "local"]
    expected_generation: int = Field(ge=1)


class ProjectLocalSyncBody(ProjectRuntimeBody):
    expected_generation: int = Field(ge=1)
    preview_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


@router.post("/dashboard-api/projects/{project_id}/local-sync")
async def synchronize_project_local(body: ProjectLocalSyncBody, project_id: int = Path(ge=1)):
    from agent.modules.projects import get_project_workspace_service
    try:
        return await get_project_workspace_service().local_sync.synchronize(
            project_id, body.expected_generation, body.project_workspace_id, preview_token=body.preview_token,
        )
    except Exception as exc:
        raise project_resource_error(exc) from exc


@router.post("/dashboard-api/projects/{project_id}/runtime/recovery")
async def confirm_project_runtime_recovery(body: ProjectRuntimeRecoveryBody, project_id: int = Path(ge=1)):
    """Apply the pending re-provision decision for a lost cloud sandbox."""
    from agent.modules.projects import get_project_workspace_service
    try:
        binding = await get_project_workspace_service().confirm_provision(
            project_id, body.action, body.expected_generation, body.project_workspace_id)
        return {"workspace": binding.model_dump()}
    except Exception as exc:
        raise project_resource_error(exc) from exc


@router.post("/dashboard-api/projects/{project_id}/checkpoint")
async def checkpoint_project(body: ProjectRuntimeBody, project_id: int = Path(ge=1)):
    from agent.modules.projects import get_project_workspace_service
    try:
        return await get_project_workspace_service().checkpoint(project_id, body.project_workspace_id)
    except Exception as exc:
        raise project_resource_error(exc) from exc


@router.post("/dashboard-api/projects/{project_id}/migration/select")
async def select_project_migration_source(body: MigrationSelectionBody, project_id: int = Path(ge=1)):
    from agent.modules.projects import get_project_workspace_service
    try:
        binding = await get_project_workspace_service().select_migration_source(project_id, body.source_id)
        return {"workspace": binding.model_dump()}
    except Exception as exc:
        raise project_resource_error(exc) from exc


def project_resource_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="Project not found.")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (ValueError, UnicodeError, IsADirectoryError)):
        return HTTPException(status_code=400, detail=str(exc))
    return workspace_http_error(exc)


@router.get("/dashboard-api/projects/{project_id}")
async def get_project_detail(project_id: int = Path(ge=1)):
    try:
        return await get_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except Exception as exc:
        logger.exception("Failed to load project")
        raise HTTPException(status_code=503, detail="Project catalog is temporarily unavailable.") from exc


@router.get("/dashboard-api/projects/{project_id}/instructions")
async def get_instructions(project_id: int = Path(ge=1)):
    try:
        return await get_project_instructions(project_id)
    except Exception as exc:
        raise project_resource_error(exc) from exc


@router.put("/dashboard-api/projects/{project_id}/instructions")
async def put_instructions(body: ProjectInstructionsBody, project_id: int = Path(ge=1)):
    try:
        return await save_project_instructions(project_id, body.content, body.expected_version)
    except Exception as exc:
        raise project_resource_error(exc) from exc


@router.get("/dashboard-api/projects")
async def get_projects(
    status: Literal["active", "archived", "removed", "all"] = "active",
    search: str = Query(default="", max_length=4096), limit: int = Query(default=20, ge=1, le=100),
    after: str | None = Query(default=None, max_length=16384),
):
    try:
        return await list_projects(status=status, search=search, limit=limit, after=after)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to load project catalog")
        raise HTTPException(status_code=503, detail="Project catalog is temporarily unavailable.") from exc


@router.post("/dashboard-api/projects")
async def create_project(body: ProjectCreateBody):
    try:
        return await add_project(body.workspace, label=body.label)
    except (KeyError, ProjectWorkspaceError) as exc:
        raise project_resource_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to add project")
        raise HTTPException(status_code=503, detail="Project catalog is temporarily unavailable.") from exc


@router.patch("/dashboard-api/projects/{project_id}")
async def patch_project(body: ProjectUpdateBody, project_id: int = Path(ge=1)):
    try:
        return await update_project(project_id, body.model_dump(exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except Exception as exc:
        logger.exception("Failed to update project")
        raise HTTPException(status_code=503, detail="Project catalog is temporarily unavailable.") from exc


@router.delete("/dashboard-api/projects/{project_id}")
async def remove_project(project_id: int = Path(ge=1)):
    try:
        return await update_project(project_id, {"status": "removed"})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except Exception as exc:
        logger.exception("Failed to remove project")
        raise HTTPException(status_code=503, detail="Project catalog is temporarily unavailable.") from exc
