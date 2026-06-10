from __future__ import annotations

from collections.abc import Callable
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
    attach_github_repository_to_workspace,
    attach_workspace_backend,
    create_workspace_backend,
    DAYTONA_BACKEND,
    ensure_workspace_directory,
    get_workspace_browser,
    get_workspace_change_inspector,
    get_workspace_entry_mutator,
    is_github_workspace,
    list_workspace_directories,
    MODAL_BACKEND,
    remember_thread_workspace_ref,
    resolve_workspace_ref,
    get_workspace_backend_registry,
)


router = APIRouter()


async def _run_workspace_capability_operation(
    workspace: WorkspaceRef,
    thread_id: str | None,
    capability_getter: Callable[..., Any],
    operation: Callable[[Any], Any],
) -> dict[str, Any]:
    capability = await capability_getter(workspace, thread_id=thread_id)
    return await operation(capability)


async def create_daytona_workspace(*, label: str | None = None) -> WorkspaceRef:
    return await create_workspace_backend(DAYTONA_BACKEND, label=label)


async def attach_daytona_workspace(
    sandbox_id: str,
    *,
    label: str | None = None,
    root: str | None = None,
) -> WorkspaceRef:
    return await attach_workspace_backend(
        DAYTONA_BACKEND,
        sandbox_id,
        label=label,
        root=root,
    )


async def create_modal_workspace(*, label: str | None = None) -> WorkspaceRef:
    return await create_workspace_backend(MODAL_BACKEND, label=label)


async def attach_modal_workspace(
    sandbox_id: str,
    *,
    label: str | None = None,
    root: str | None = None,
) -> WorkspaceRef:
    return await attach_workspace_backend(
        MODAL_BACKEND,
        sandbox_id,
        label=label,
        root=root,
    )


def _workspace_metadata_root(workspace: WorkspaceRef | None) -> str | None:
    if workspace is None:
        return None
    return str(workspace.metadata.get("root") or "").strip() or None


@router.get("/dashboard-api/workspace/default")
async def get_dashboard_default_workspace() -> dict[str, Any]:
    """Get the default workspace reference (typically the local working directory)."""
    return {"workspace": resolve_workspace_ref(None).model_dump()}


@router.get("/dashboard-api/workspace/browse")
async def browse_dashboard_workspace(
    path: str | None = Query(default=None, description="Directory path to browse. Omit for default."),
) -> dict[str, Any]:
    """Browse workspace directories at the given path."""
    try:
        return list_workspace_directories(path)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/tree")
async def get_dashboard_workspace_tree(
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
    path: str = Query(default="", description="Relative path within the workspace."),
) -> dict[str, Any]:
    """Get the file tree for a workspace at the given path."""
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        async def _op(b): return await b.tree(path)
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_browser,
            _op,
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/changes")
async def get_dashboard_workspace_changes(
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
) -> dict[str, Any]:
    """Get the list of uncommitted changes in the workspace."""
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        async def _op(b): return await b.changes()
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_change_inspector,
            _op,
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/diff")
async def get_dashboard_workspace_diff(
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
    path: str = Query(..., min_length=1, description="File path to get the diff for."),
) -> dict[str, Any]:
    """Get the diff for a specific file in the workspace."""
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        async def _op(b): return await b.diff(path)
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_change_inspector,
            _op,
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/file")
async def get_dashboard_workspace_file(
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
    path: str = Query(..., min_length=1, description="File path to read."),
) -> dict[str, Any]:
    """Read the contents of a file in the workspace."""
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root,
        )
        async def _op(b): return await b.file(path)
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_browser,
            _op,
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


class WorkspaceRenameBody(BaseModel):
    """Request body for renaming a file or directory in the workspace."""

    thread_id: str | None = Field(default=None, description="Thread ID to resolve workspace from.")
    workspace: WorkspaceRef | None = Field(default=None, description="Workspace reference.")
    path: str = Field(..., min_length=1, description="Current path of the file or directory to rename.")
    new_name: str = Field(..., min_length=1, description="New name for the file or directory.")


@router.post("/dashboard-api/workspace/rename")
async def rename_dashboard_workspace_entry(
    body: WorkspaceRenameBody,
) -> dict[str, Any]:
    """Rename a file or directory in the workspace."""
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=body.thread_id,
            workspace=body.workspace,
        )
        async def _op(b): return await b.rename(path=body.path, new_name=body.new_name)
        return await _run_workspace_capability_operation(
            workspace,
            body.thread_id,
            get_workspace_entry_mutator,
            _op,
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


class WorkspaceDeleteBody(BaseModel):
    """Request body for deleting a file or directory in the workspace."""

    thread_id: str | None = Field(default=None, description="Thread ID to resolve workspace from.")
    workspace: WorkspaceRef | None = Field(default=None, description="Workspace reference.")
    path: str = Field(..., min_length=1, description="Path of the file or directory to delete.")


class WorkspaceResolveBody(BaseModel):
    """Request body for resolving a workspace from various inputs (local, GitHub, sandbox)."""

    kind: str | None = Field(default=None, description="Workspace kind hint (e.g. 'github', 'local').")
    backend: str | None = Field(default=None, description="Explicit backend name ('local', 'daytona', 'modal').")
    workspace: WorkspaceRef | None = Field(default=None, description="Existing workspace reference.")
    locator: str | None = Field(default=None, description="Backend-specific locator (e.g. sandbox ID).")
    repository_id: int | None = Field(default=None, description="GitHub repository ID to attach.")
    thread_id: str | None = Field(default=None, description="Thread ID to remember the resolved workspace for.")


def _resolve_backend(body: WorkspaceResolveBody, kind: str) -> str:
    """Return the target backend for the resolve request.

    Prefers an explicit ``backend`` field on the request body, then the
    ``workspace`` hint, then the request ``kind`` so that ``kind="github"``
    continues to work for the local-only flow.
    """
    known_backends = set(get_workspace_backend_registry().names())
    if body.backend and body.backend.strip().lower() in known_backends:
        return body.backend.strip().lower()
    if body.workspace and body.workspace.backend in known_backends:
        return body.workspace.backend
    if kind in known_backends:
        return kind
    return "local"


@router.post("/dashboard-api/workspace/resolve")
async def resolve_dashboard_workspace(body: WorkspaceResolveBody) -> dict[str, Any]:
    """Resolve a workspace from the given inputs. Supports local, GitHub, Daytona, and Modal backends."""
    kind_source = body.kind or (body.workspace.backend if body.workspace else "local")
    kind = kind_source.strip().lower()
    repository_id = body.repository_id
    try:
        if kind == "github":
            if repository_id is None:
                raise ValueError("Repository ID is required.")
            backend = _resolve_backend(body, kind)
            if backend == "daytona":
                workspace = await _resolve_github_in_sandbox(
                    body,
                    backend=backend,
                    repository_id=repository_id,
                )
                return await _remember_and_respond(
                    body=body,
                    workspace=workspace,
                    kind=backend,
                )
            if backend == "modal":
                workspace = await _resolve_github_in_sandbox(
                    body,
                    backend=backend,
                    repository_id=repository_id,
                )
                return await _remember_and_respond(
                    body=body,
                    workspace=workspace,
                    kind=backend,
                )
            result = await get_github_automation_service().resolve_repository_workspace(
                repository_id,
            )
            if body.thread_id and body.thread_id.strip():
                payload = result.get("workspace")
                if payload:
                    workspace = await remember_thread_workspace_ref(body.thread_id, payload)
                    result["workspace"] = workspace.model_dump()
                    result["label"] = workspace.label
            return result
        if kind == "local":
            ref = resolve_workspace_ref(
                body.workspace or {"backend": "local", "locator": body.locator}
            )
            if repository_id is not None:
                workspace = await attach_github_repository_to_workspace(
                    ref,
                    repository_id=repository_id,
                )
            else:
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
        if kind == "daytona":
            sandbox_id = body.locator or (body.workspace.locator if body.workspace else "")
            if sandbox_id and sandbox_id.strip():
                root = _workspace_metadata_root(body.workspace)
                workspace = await attach_daytona_workspace(
                    sandbox_id,
                    root=root,
                )
            else:
                workspace = await create_daytona_workspace()
            if repository_id is not None:
                workspace = await attach_github_repository_to_workspace(
                    workspace,
                    repository_id=repository_id,
                )
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
                workspace = await attach_modal_workspace(
                    sandbox_id,
                    root=root,
                )
            else:
                workspace = await create_modal_workspace()
            if repository_id is not None:
                workspace = await attach_github_repository_to_workspace(
                    workspace,
                    repository_id=repository_id,
                )
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


async def _resolve_github_in_sandbox(
    body: WorkspaceResolveBody,
    *,
    backend: str,
    repository_id: int,
) -> WorkspaceRef:
    """Create or attach a sandbox then clone a GitHub repository inside it."""
    if backend == "daytona":
        sandbox_id = body.locator or (body.workspace.locator if body.workspace else "")
        if sandbox_id and sandbox_id.strip():
            root = _workspace_metadata_root(body.workspace)
            workspace = await attach_daytona_workspace(
                sandbox_id,
                root=root,
            )
        else:
            workspace = await create_daytona_workspace()
    elif backend == "modal":
        sandbox_id = body.locator or (body.workspace.locator if body.workspace else "")
        if sandbox_id and sandbox_id.strip():
            root = _workspace_metadata_root(body.workspace)
            workspace = await attach_modal_workspace(
                sandbox_id,
                root=root,
            )
        else:
            workspace = await create_modal_workspace()
    else:
        raise ValueError(f"Unsupported sandbox backend: {backend}")
    return await attach_github_repository_to_workspace(
        workspace,
        repository_id=repository_id,
    )


async def _remember_and_respond(
    *,
    body: WorkspaceResolveBody,
    workspace: WorkspaceRef,
    kind: str,
) -> dict[str, Any]:
    if body.thread_id and body.thread_id.strip():
        workspace = await remember_thread_workspace_ref(body.thread_id, workspace)
    payload = workspace.model_dump()
    return {
        "kind": kind,
        "label": workspace.display_label() or workspace.label,
        "workspace": payload,
        "is_github_source": is_github_workspace(payload),
    }


class WorkspaceCreateDirBody(BaseModel):
    """Request body for creating a new directory in the workspace."""

    parent_path: str = Field(..., min_length=1, description="Parent directory path where the new directory will be created.")
    name: str = Field(..., min_length=1, description="Name of the new directory.")


@router.post("/dashboard-api/workspace/create-dir")
async def create_dashboard_workspace_directory(
    body: WorkspaceCreateDirBody,
) -> dict[str, Any]:
    """Create a new directory inside the workspace."""
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
    """Delete a file or directory in the workspace."""
    try:
        workspace = await _workspace_ref_from_request(
            thread_id=body.thread_id,
            workspace=body.workspace,
        )
        async def _op(b): return await b.delete(path=body.path)
        return await _run_workspace_capability_operation(
            workspace,
            body.thread_id,
            get_workspace_entry_mutator,
            _op,
        )
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
