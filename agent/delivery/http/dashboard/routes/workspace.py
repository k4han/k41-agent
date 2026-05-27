from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.modules.workspaces import WorkspaceRef
from agent.delivery.http.dashboard.routes.shared import (
    _workspace_http_error,
    _workspace_ref_from_request,
)
from agent.modules.github import get_github_automation_service
from agent.modules.workspaces import (
    ensure_workspace_directory,
    get_workspace_backend,
    list_workspace_directories,
    resolve_workspace_ref,
)


router = APIRouter()


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
