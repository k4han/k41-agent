from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent.modules.workspaces import WorkspaceRef
from agent.delivery.http.dashboard.routes.shared import (
    _workspace_http_error,
    _workspace_ref_from_request,
)
from agent.modules.github import get_github_automation_service
from agent.modules.workspaces import (
    attach_daytona_workspace,
    attach_modal_workspace,
    create_daytona_workspace,
    create_modal_workspace,
    ensure_workspace_directory,
    get_workspace_backend,
    list_workspace_directories,
    remember_thread_workspace_ref,
    resolve_workspace_ref,
)


router = APIRouter()


async def _run_workspace_backend_operation(
    workspace: WorkspaceRef,
    thread_id: str | None,
    operation: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        backend = get_workspace_backend(workspace, thread_id=thread_id)
        return operation(backend)

    return await run_in_threadpool(run)


def _workspace_metadata_root(workspace: WorkspaceRef | None) -> str | None:
    if workspace is None:
        return None
    return str(workspace.metadata.get("root") or "").strip() or None


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
    root: str | None = Query(default=None),
    path: str = Query(default=""),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        return await _run_workspace_backend_operation(
            workspace,
            thread_id,
            lambda backend: backend.tree(path),
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/changes")
async def get_dashboard_workspace_changes(
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
    root: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        return await _run_workspace_backend_operation(
            workspace,
            thread_id,
            lambda backend: backend.changes(),
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/diff")
async def get_dashboard_workspace_diff(
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
    root: str | None = Query(default=None),
    path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        return await _run_workspace_backend_operation(
            workspace,
            thread_id,
            lambda backend: backend.diff(path),
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/file")
async def get_dashboard_workspace_file(
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
    root: str | None = Query(default=None),
    path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        return await _run_workspace_backend_operation(
            workspace,
            thread_id,
            lambda backend: backend.file(path),
        )
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
        return await _run_workspace_backend_operation(
            workspace,
            body.thread_id,
            lambda backend: backend.rename(path=body.path, new_name=body.new_name),
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
    thread_id: str | None = None


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
            if body.thread_id and body.thread_id.strip():
                workspace = await remember_thread_workspace_ref(body.thread_id, workspace)
            return {
                "kind": "local",
                "label": workspace.label,
                "workspace": workspace.model_dump(),
            }
        if kind == "github":
            if body.repository_id is None:
                raise ValueError("Repository ID is required.")
            result = await get_github_automation_service().resolve_repository_workspace(
                body.repository_id,
            )
            if body.thread_id and body.thread_id.strip():
                payload = result.get("workspace")
                if payload:
                    workspace = await remember_thread_workspace_ref(body.thread_id, payload)
                    result["workspace"] = workspace.model_dump()
                    result["label"] = workspace.label
            return result
        if kind == "daytona":
            sandbox_id = body.locator or (body.workspace.locator if body.workspace else "")
            if sandbox_id and sandbox_id.strip():
                root = _workspace_metadata_root(body.workspace)
                workspace = await run_in_threadpool(
                    attach_daytona_workspace,
                    sandbox_id,
                    root=root,
                )
            else:
                workspace = await run_in_threadpool(create_daytona_workspace)
            if body.thread_id and body.thread_id.strip():
                workspace = await remember_thread_workspace_ref(body.thread_id, workspace)
            return {
                "kind": "daytona",
                "label": workspace.label,
                "workspace": workspace.model_dump(),
            }
        if kind == "modal":
            sandbox_id = body.locator or (body.workspace.locator if body.workspace else "")
            if sandbox_id and sandbox_id.strip():
                root = _workspace_metadata_root(body.workspace)
                workspace = await run_in_threadpool(
                    attach_modal_workspace,
                    sandbox_id,
                    root=root,
                )
            else:
                workspace = await run_in_threadpool(create_modal_workspace)
            if body.thread_id and body.thread_id.strip():
                workspace = await remember_thread_workspace_ref(body.thread_id, workspace)
            return {
                "kind": "modal",
                "label": workspace.label,
                "workspace": workspace.model_dump(),
            }
        raise ValueError(f"Unsupported workspace kind: {body.kind}")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


class WorkspaceCreateDirBody(BaseModel):
    parent_path: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


@router.post("/dashboard-api/workspace/create-dir")
async def create_dashboard_workspace_directory(
    body: WorkspaceCreateDirBody,
) -> dict[str, Any]:
    try:
        from pathlib import Path
        parent = Path(body.parent_path.strip()).expanduser().resolve()
        if not parent.exists():
            raise FileNotFoundError(f"Parent directory does not exist: {parent}")
        if not parent.is_dir():
            raise NotADirectoryError(f"Parent path is not a directory: {parent}")
        
        clean_name = body.name.strip()
        if not clean_name or clean_name in {".", ".."}:
            raise ValueError(f"Invalid directory name: {body.name}")
        if any(char in clean_name for char in ("/", "\\")):
            raise ValueError("Directory name cannot contain path separators.")
            
        new_dir = parent / clean_name
        if new_dir.exists():
            raise FileExistsError(f"Directory already exists: {clean_name}")
            
        new_dir.mkdir(parents=False, exist_ok=False)
        return {
            "success": True,
            "path": str(new_dir.resolve()),
            "name": clean_name,
        }
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
        return await _run_workspace_backend_operation(
            workspace,
            body.thread_id,
            lambda backend: backend.delete(path=body.path),
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
