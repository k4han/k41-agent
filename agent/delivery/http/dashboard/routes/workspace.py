from __future__ import annotations

from collections.abc import Callable
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.modules.workspaces import WorkspaceBinding, WorkspaceRef
from agent.delivery.http.dashboard.routes.helpers.workspace import (
    workspace_http_error,
    workspace_ref_from_request,
)
from agent.modules.github import get_github_automation_service
from agent.modules.workspaces import (
    attach_github_repository_to_workspace,
    attach_workspace_backend,
    bind_workspace_ref,
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

INLINE_FILE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
    ".bmp": "image/bmp", ".ico": "image/x-icon", ".svg": "image/svg+xml",
    ".pdf": "application/pdf", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".m4a": "audio/mp4",
    ".flac": "audio/flac", ".aac": "audio/aac", ".mp4": "video/mp4",
    ".webm": "video/webm", ".ogv": "video/ogg", ".mov": "video/quicktime",
}


async def _workspace_file_response(
    workspace: WorkspaceRef, thread_id: str | None, path: str, *, inline: bool,
) -> Response:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    media_type = INLINE_FILE_TYPES.get(Path(name).suffix.lower()) if inline else (
        mimetypes.guess_type(name)[0] or "application/octet-stream"
    )
    if not media_type:
        raise HTTPException(status_code=415, detail="This file type has no inline preview.")
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name, safe='')}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "X-Workspace-Data-Source": "live",
    }
    if inline and media_type == "image/svg+xml":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    if workspace.backend == "local":
        from agent.modules.projects import get_project_workspace_service
        projects = get_project_workspace_service()
        if projects.container._persistence_ready:
            binding = await projects.find_binding(workspace, thread_id=thread_id)
            if binding and binding.runtime_generation is not None:
                headers["X-Workspace-Generation"] = str(binding.runtime_generation)
        from agent.modules.tools import resolve_safe_path
        from agent.modules.workspaces import resolve_workspace_root

        root = resolve_workspace_root(workspace.locator)
        full_path = Path(resolve_safe_path(str(root), path))
        if not full_path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return FileResponse(full_path, media_type=media_type, headers=headers)

    from agent.modules.workspaces import get_workspace_file_io

    file_io = await get_workspace_file_io(workspace, thread_id=thread_id)
    read_result = getattr(file_io, "read_result", None)
    if read_result is not None:
        result = await read_result("read_bytes", path, consistency="saved_ok")
        headers.update(result.headers())
        return Response(content=result.data, media_type=media_type, headers=headers)
    return Response(content=await file_io.read_bytes(path), media_type=media_type, headers=headers)


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
    return {"workspace": bind_workspace_ref(resolve_workspace_ref(None)).model_dump()}


@router.get("/dashboard-api/workspace/browse")
async def browse_dashboard_workspace(
    path: str | None = Query(default=None, description="Directory path to browse. Omit for default."),
) -> dict[str, Any]:
    """Browse workspace directories at the given path."""
    try:
        return list_workspace_directories(path)
    except Exception as exc:
        raise workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/tree")
async def get_dashboard_workspace_tree(
    project_id: int | None = None, project_workspace_id: int | None = None,
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
    path: str = Query(default="", description="Relative path within the workspace."),
) -> dict[str, Any]:
    """Get the file tree for a workspace at the given path."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root, project_id=project_id, project_workspace_id=project_workspace_id,
        )
        async def _op(b): return await b.tree(path)
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_browser,
            _op,
        )
    except Exception as exc:
        raise workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/changes")
async def get_dashboard_workspace_changes(
    project_id: int | None = None, project_workspace_id: int | None = None,
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
) -> dict[str, Any]:
    """Get the list of uncommitted changes in the workspace."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root, project_id=project_id, project_workspace_id=project_workspace_id,
        )
        async def _op(b): return await b.changes()
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_change_inspector,
            _op,
        )
    except Exception as exc:
        raise workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/diff")
async def get_dashboard_workspace_diff(
    project_id: int | None = None, project_workspace_id: int | None = None,
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
    path: str = Query(..., min_length=1, description="File path to get the diff for."),
) -> dict[str, Any]:
    """Get the diff for a specific file in the workspace."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root, project_id=project_id, project_workspace_id=project_workspace_id,
        )
        async def _op(b): return await b.diff(path)
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_change_inspector,
            _op,
        )
    except Exception as exc:
        raise workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/file")
async def get_dashboard_workspace_file(
    project_id: int | None = None, project_workspace_id: int | None = None,
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
    path: str = Query(..., min_length=1, description="File path to read."),
) -> dict[str, Any]:
    """Read the contents of a file in the workspace."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root, project_id=project_id, project_workspace_id=project_workspace_id,
        )
        async def _op(b): return await b.file(path)
        return await _run_workspace_capability_operation(
            workspace,
            thread_id,
            get_workspace_browser,
            _op,
        )
    except Exception as exc:
        raise workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/download")
async def download_dashboard_workspace_file(
    project_id: int | None = None, project_workspace_id: int | None = None,
    thread_id: str | None = Query(default=None, description="Thread ID to resolve workspace from."),
    backend: str | None = Query(default="local", description="Workspace backend type."),
    locator: str | None = Query(default=None, description="Backend-specific locator."),
    root: str | None = Query(default=None, description="Root directory override."),
    path: str = Query(..., min_length=1, description="File path to download."),
) -> Response:
    """Download a file (binary or text) from the workspace."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=thread_id,
            backend=backend,
            locator=locator,
            root=root, project_id=project_id, project_workspace_id=project_workspace_id,
        )
        return await _workspace_file_response(workspace, thread_id, path, inline=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise workspace_http_error(exc) from exc


@router.get("/dashboard-api/workspace/preview")
async def preview_dashboard_workspace_file(
    project_id: int | None = None, project_workspace_id: int | None = None,
    thread_id: str | None = Query(default=None),
    backend: str | None = Query(default="local"),
    locator: str | None = Query(default=None),
    root: str | None = Query(default=None),
    path: str = Query(..., min_length=1),
) -> Response:
    """Serve browser-supported binary formats without the text preview limit."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=thread_id, backend=backend, locator=locator, root=root,
            project_id=project_id, project_workspace_id=project_workspace_id,
        )
        return await _workspace_file_response(workspace, thread_id, path, inline=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise workspace_http_error(exc) from exc


class WorkspaceProjectBody(BaseModel):
    project_id: int | None = Field(default=None, ge=1)
    project_workspace_id: int | None = Field(default=None, ge=1)


class WorkspaceRenameBody(WorkspaceProjectBody):
    """Request body for renaming a file or directory in the workspace."""

    thread_id: str | None = Field(default=None, description="Thread ID to resolve workspace from.")
    workspace: WorkspaceRef | WorkspaceBinding | None = Field(default=None, description="Workspace reference.")
    path: str = Field(..., min_length=1, description="Current path of the file or directory to rename.")
    new_name: str = Field(..., min_length=1, description="New name for the file or directory.")


@router.post("/dashboard-api/workspace/rename")
async def rename_dashboard_workspace_entry(
    body: WorkspaceRenameBody,
) -> dict[str, Any]:
    """Rename a file or directory in the workspace."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=body.thread_id,
            workspace=body.workspace, project_id=body.project_id, project_workspace_id=body.project_workspace_id,
        )
        async def _op(b): return await b.rename(path=body.path, new_name=body.new_name)
        return await _run_workspace_capability_operation(
            workspace,
            body.thread_id,
            get_workspace_entry_mutator,
            _op,
        )
    except Exception as exc:
        raise workspace_http_error(exc) from exc


class WorkspaceDeleteBody(WorkspaceProjectBody):
    """Request body for deleting a file or directory in the workspace."""

    thread_id: str | None = Field(default=None, description="Thread ID to resolve workspace from.")
    workspace: WorkspaceRef | WorkspaceBinding | None = Field(default=None, description="Workspace reference.")
    path: str = Field(..., min_length=1, description="Path of the file or directory to delete.")


class WorkspaceCreateEntryBody(WorkspaceProjectBody):
    """Request body for creating a file or directory in the workspace."""

    thread_id: str | None = Field(default=None, description="Thread ID to resolve workspace from.")
    workspace: WorkspaceRef | WorkspaceBinding | None = Field(default=None, description="Workspace reference.")
    path: str = Field(..., min_length=1, description="Path of the file or directory to create.")
    kind: str = Field(default="file", description="Kind of entry: 'file' or 'directory'.")
    content: str = Field(default="", description="Initial content for the file (if kind is 'file').")


class WorkspaceResolveBody(WorkspaceProjectBody):
    """Request body for resolving a workspace from various inputs (local, GitHub, sandbox)."""

    kind: str | None = Field(default=None, description="Workspace kind hint (e.g. 'github', 'local').")
    backend: str | None = Field(default=None, description="Explicit backend name ('local', 'daytona', 'modal').")
    workspace: WorkspaceRef | WorkspaceBinding | None = Field(default=None, description="Existing workspace reference.")
    locator: str | None = Field(default=None, description="Backend-specific locator (e.g. sandbox ID).")
    repository_id: int | None = Field(default=None, description="GitHub repository ID to attach.")
    thread_id: str | None = Field(default=None, description="Thread ID to remember the resolved workspace for.")


def _resolve_backend(body: WorkspaceResolveBody, kind: str) -> str:
    """Return the target backend for the resolve request.

    GitHub repository resolution requires an explicit backend so local and
    sandbox flows cannot be mixed accidentally.
    """
    known_backends = set(get_workspace_backend_registry().names())
    if body.backend and body.backend.strip().lower() in known_backends:
        return body.backend.strip().lower()
    if kind == "github":
        raise ValueError("GitHub workspace resolution requires an explicit backend.")
    if body.workspace:
        ref = resolve_workspace_ref(body.workspace)
        if ref.backend in known_backends:
            return ref.backend
    if kind in known_backends:
        return kind
    return "local"


@router.post("/dashboard-api/workspace/resolve")
async def resolve_dashboard_workspace(request: Request, body: WorkspaceResolveBody) -> dict[str, Any]:
    """Resolve a workspace from the given inputs. Supports local, GitHub, Daytona, and Modal backends."""
    kind_source = body.kind or (
        resolve_workspace_ref(body.workspace).backend if body.workspace else "local"
    )
    kind = kind_source.strip().lower()
    repository_id = body.repository_id
    try:
        if body.project_id:
            from agent.modules.projects import get_project_workspace_service, ProjectWorkspaceError
            projects = get_project_workspace_service()
            ref = await workspace_ref_from_request(
                workspace=body.workspace, backend=body.backend, locator=body.locator,
                project_id=body.project_id, project_workspace_id=body.project_workspace_id,
            )
            if repository_id is not None and str(ref.metadata.get("repository_id", "")) != str(repository_id):
                raise ProjectWorkspaceError("Repository reference conflicts with the project.", "workspace_conflict")
            binding = await projects.binding(body.project_id, body.project_workspace_id)
            if body.thread_id:
                await remember_thread_workspace_ref(body.thread_id, binding)
            return {"kind": binding.execution.backend, "label": binding.scope.label, "workspace": binding.model_dump()}
        from agent.modules.projects import get_project_workspace_service, ProjectWorkspaceError
        projects = get_project_workspace_service()
        if projects.container._persistence_ready:
            binding = None
            if kind != "github" and (body.workspace or body.locator):
                requested = body.workspace or {"backend": body.backend or kind, "locator": body.locator}
                binding = await projects.find_binding(requested)
            if binding is None and repository_id is not None:
                binding = await projects.find_repository_binding(repository_id)
            if binding is not None:
                # A project may be registered by key only, so the repository id can live on either ref.
                declared = binding.execution.metadata.get("repository_id")
                if declared is None:
                    declared = binding.scope.metadata.get("repository_id")
                if repository_id is not None and declared is not None and str(declared) != str(repository_id):
                    raise ProjectWorkspaceError("Repository reference conflicts with the project.", "workspace_conflict")
                if body.thread_id:
                    await remember_thread_workspace_ref(body.thread_id, binding)
                return {"kind": binding.execution.backend, "label": binding.scope.label,
                        "workspace": binding.model_dump(), "reused_project": True}
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
            result = await get_github_automation_service(request).resolve_repository_workspace(
                repository_id,
            )
            payload = result.get("workspace")
            if payload:
                if body.thread_id and body.thread_id.strip():
                    workspace = await remember_thread_workspace_ref(
                        body.thread_id,
                        payload,
                    )
                else:
                    workspace = resolve_workspace_ref(payload)
                binding = bind_workspace_ref(workspace)
                result["workspace"] = binding.model_dump()
                result["label"] = binding.scope.label
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
            binding = bind_workspace_ref(workspace)
            return {
                "kind": "local",
                "label": binding.scope.label,
                "workspace": binding.model_dump(),
            }
        if kind == "daytona":
            body_ref = resolve_workspace_ref(body.workspace) if body.workspace else None
            sandbox_id = body.locator or (body_ref.locator if body_ref else "")
            if sandbox_id and sandbox_id.strip():
                root = _workspace_metadata_root(body_ref)
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
            binding = bind_workspace_ref(workspace)
            return {
                "kind": "daytona",
                "label": binding.scope.label,
                "workspace": binding.model_dump(),
            }
        if kind == "modal":
            body_ref = resolve_workspace_ref(body.workspace) if body.workspace else None
            sandbox_id = body.locator or (body_ref.locator if body_ref else "")
            if sandbox_id and sandbox_id.strip():
                root = _workspace_metadata_root(body_ref)
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
            binding = bind_workspace_ref(workspace)
            return {
                "kind": "modal",
                "label": binding.scope.label,
                "workspace": binding.model_dump(),
            }
        raise ValueError(f"Unsupported workspace kind: {body.kind}")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise workspace_http_error(exc) from exc


async def _resolve_github_in_sandbox(
    body: WorkspaceResolveBody,
    *,
    backend: str,
    repository_id: int,
) -> WorkspaceRef:
    """Create or attach a sandbox then clone a GitHub repository inside it."""
    if backend == "daytona":
        body_ref = resolve_workspace_ref(body.workspace) if body.workspace else None
        sandbox_id = body.locator or (body_ref.locator if body_ref else "")
        if sandbox_id and sandbox_id.strip():
            root = _workspace_metadata_root(body_ref)
            workspace = await attach_daytona_workspace(
                sandbox_id,
                root=root,
            )
        else:
            workspace = await create_daytona_workspace()
    elif backend == "modal":
        body_ref = resolve_workspace_ref(body.workspace) if body.workspace else None
        sandbox_id = body.locator or (body_ref.locator if body_ref else "")
        if sandbox_id and sandbox_id.strip():
            root = _workspace_metadata_root(body_ref)
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
    binding = bind_workspace_ref(workspace)
    payload = binding.model_dump()
    return {
        "kind": kind,
        "label": binding.scope.label,
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
        raise workspace_http_error(exc) from exc


@router.post("/dashboard-api/workspace/delete")
async def delete_dashboard_workspace_entry(
    body: WorkspaceDeleteBody,
) -> dict[str, Any]:
    """Delete a file or directory in the workspace."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=body.thread_id,
            workspace=body.workspace, project_id=body.project_id, project_workspace_id=body.project_workspace_id,
        )
        async def _op(b): return await b.delete(path=body.path)
        return await _run_workspace_capability_operation(
            workspace,
            body.thread_id,
            get_workspace_entry_mutator,
            _op,
        )
    except Exception as exc:
        raise workspace_http_error(exc) from exc


@router.post("/dashboard-api/workspace/create-entry")
async def create_dashboard_workspace_entry(
    body: WorkspaceCreateEntryBody,
) -> dict[str, Any]:
    """Create a new file or directory in the workspace."""
    try:
        workspace = await workspace_ref_from_request(
            thread_id=body.thread_id,
            workspace=body.workspace, project_id=body.project_id, project_workspace_id=body.project_workspace_id,
        )
        clean_kind = body.kind.strip().lower()
        if clean_kind in ("dir", "directory", "folder"):
            async def _op(b): return await b.create_directory(path=body.path)
        elif clean_kind == "file":
            async def _op(b): return await b.create_file(path=body.path, content=body.content)
        else:
            raise ValueError(f"Unsupported entry kind: '{body.kind}'. Expected 'file' or 'directory'.")

        return await _run_workspace_capability_operation(
            workspace,
            body.thread_id,
            get_workspace_entry_mutator,
            _op,
        )
    except Exception as exc:
        raise workspace_http_error(exc) from exc
