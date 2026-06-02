"""Helpers that attach a GitHub repository to any workspace backend.

The previous design treated GitHub repositories as a separate workspace kind
alongside ``local``/``daytona``/``modal`` which forced every GitHub-backed
task to run on the host filesystem. This module keeps the same business
semantics for local workspaces but also supports cloning the repository
inside a Daytona or Modal sandbox so the agent can pick the execution
backend independently of the source repository.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.modules.workspaces.refs import (
    DEFAULT_LOCAL_WORKSPACE,
    WorkspaceRef,
    normalize_workspace_ref,
)
from agent.modules.workspaces.service import workspace_ref_from_local_path

if TYPE_CHECKING:
    from agent.modules.github import GitHubWorkspaceManager

logger = logging.getLogger(__name__)

GITHUB_REPO_FULL_NAME_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


def _get_github_workspace_manager_cls():
    from agent.modules.github import GitHubWorkspaceManager

    return GitHubWorkspaceManager


@dataclass(frozen=True, slots=True)
class GitHubRepositorySelection:
    """Lightweight description of the repository we want to materialize."""

    repository_id: int
    full_name: str
    default_branch: str = "main"
    installation_id: int = 0
    token: str = ""

    @classmethod
    def from_binding(cls, binding: Any) -> "GitHubRepositorySelection":
        return cls(
            repository_id=int(getattr(binding, "repository_id", 0) or 0),
            full_name=str(getattr(binding, "full_name", "") or "").strip(),
            default_branch=str(
                getattr(binding, "default_branch", "") or "main"
            ).strip()
            or "main",
            installation_id=int(getattr(binding, "installation_id", 0) or 0),
        )


def _github_workspace_metadata(
    selection: GitHubRepositorySelection,
    *,
    repository_path: str,
) -> dict[str, Any]:
    return {
        "source": "github",
        "repository_id": selection.repository_id,
        "repository_full_name": selection.full_name,
        "default_branch": selection.default_branch,
        "repository_path": repository_path,
    }


def _split_full_name(full_name: str) -> tuple[str, str]:
    cleaned = (full_name or "").strip()
    if not GITHUB_REPO_FULL_NAME_RE.match(cleaned):
        raise ValueError(f"Invalid GitHub repository full name: {full_name!r}")
    owner, repo = cleaned.split("/", 1)
    return owner, repo


def _ensure_local_checkout(
    selection: GitHubRepositorySelection,
    *,
    manager: GitHubWorkspaceManager,
    token: str,
) -> Path:
    """Run the async ``ensure_shared_checkout`` from a sync context."""

    async def _runner() -> Path:
        return await manager.ensure_shared_checkout(
            full_name=selection.full_name,
            token=token,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_runner(), loop)
        return Path(future.result())
    return Path(asyncio.run(_runner()))


async def attach_github_repository_to_local_workspace_async(
    selection: GitHubRepositorySelection,
    *,
    manager: GitHubWorkspaceManager | None = None,
    token: str | None = None,
) -> WorkspaceRef:
    """Clone a GitHub repository to a local path and return a local WorkspaceRef.

    Mirrors the historical behavior where GitHub repos were always materialized
    under ``~/kaka-agent/github-workspaces/{owner}/{repo}``.
    """
    manager = manager or _get_github_workspace_manager_cls()()
    _split_full_name(selection.full_name)
    clean_token = (token or selection.token or "").strip()
    absolute_path = str(
        await manager.ensure_shared_checkout(
            full_name=selection.full_name,
            token=clean_token,
        )
    )
    return workspace_ref_from_local_path(
        absolute_path,
        label=selection.full_name,
        metadata=_github_workspace_metadata(selection, repository_path=absolute_path),
    )


def attach_github_repository_to_local_workspace(
    selection: GitHubRepositorySelection,
    *,
    manager: GitHubWorkspaceManager | None = None,
    token: str | None = None,
) -> WorkspaceRef:
    """Synchronous wrapper around :func:`attach_github_repository_to_local_workspace_async`."""

    manager = manager or _get_github_workspace_manager_cls()()
    _split_full_name(selection.full_name)
    clean_token = (token or selection.token or "").strip()
    absolute_path = str(
        _ensure_local_checkout(selection, manager=manager, token=clean_token)
    )
    return workspace_ref_from_local_path(
        absolute_path,
        label=selection.full_name,
        metadata=_github_workspace_metadata(selection, repository_path=absolute_path),
    )


async def attach_github_repository_to_daytona_workspace(
    workspace: WorkspaceRef,
    selection: GitHubRepositorySelection,
    *,
    token: str | None = None,
) -> WorkspaceRef:
    """Clone a GitHub repository inside a Daytona sandbox."""
    from agent.modules.workspaces.service import get_workspace_repository_cloner

    owner, repo = _split_full_name(selection.full_name)
    cloner = await get_workspace_repository_cloner(workspace)
    relative_path = await cloner.clone_repository(
        owner=owner,
        repo=repo,
        default_branch=selection.default_branch,
        token=(token or selection.token or "").strip() or None,
    )
    return _with_github_metadata(workspace, selection, repository_path=relative_path)


async def attach_github_repository_to_modal_workspace(
    workspace: WorkspaceRef,
    selection: GitHubRepositorySelection,
    *,
    token: str | None = None,
) -> WorkspaceRef:
    """Clone a GitHub repository inside a Modal sandbox."""
    from agent.modules.workspaces.service import get_workspace_repository_cloner

    owner, repo = _split_full_name(selection.full_name)
    cloner = await get_workspace_repository_cloner(workspace)
    relative_path = await cloner.clone_repository(
        owner=owner,
        repo=repo,
        default_branch=selection.default_branch,
        token=(token or selection.token or "").strip() or None,
    )
    return _with_github_metadata(workspace, selection, repository_path=relative_path)


def _with_github_metadata(
    workspace: WorkspaceRef,
    selection: GitHubRepositorySelection,
    *,
    repository_path: str,
) -> WorkspaceRef:
    metadata = dict(workspace.metadata or {})
    metadata.update(
        _github_workspace_metadata(selection, repository_path=repository_path)
    )
    label = selection.full_name
    return WorkspaceRef(
        backend=workspace.backend,
        locator=workspace.locator,
        label=label,
        metadata=metadata,
    )


async def _resolve_selection(repository_id: int) -> GitHubRepositorySelection:
    from agent.modules.github import get_github_automation_service

    service = get_github_automation_service()
    binding = await service.store.get_binding_by_repository_id(repository_id)
    if binding is None:
        raise KeyError(f"GitHub repository '{repository_id}' is not synced.")
    return GitHubRepositorySelection.from_binding(binding)


async def _fetch_installation_token(installation_id: int) -> str:
    from agent.modules.github import get_github_automation_service

    return await get_github_automation_service().client.get_installation_token(
        installation_id
    )


async def _resolve_token(selection: GitHubRepositorySelection) -> str:
    if selection.token:
        return selection.token
    if not selection.installation_id:
        return ""
    return await _fetch_installation_token(selection.installation_id)


async def attach_github_repository_to_workspace(
    workspace: WorkspaceRef,
    *,
    repository_id: int,
    install_token: str | None = None,
) -> WorkspaceRef:
    """Attach a GitHub repository to any workspace backend.

    For local workspaces the repository is cloned to a shared local path.
    For Daytona/Modal sandboxes the repository is cloned inside the sandbox.
    The returned :class:`WorkspaceRef` keeps the original ``backend`` and
    ``locator`` and stores the GitHub source under ``metadata.source``.
    """
    selection = await _resolve_selection(repository_id)
    backend = workspace.backend
    clean_token = (install_token or "").strip()
    token = clean_token or await _resolve_token(selection)

    if backend == "local":
        return await attach_github_repository_to_local_workspace_async(
            selection,
            token=token or None,
        )
    if backend == "daytona":
        return await attach_github_repository_to_daytona_workspace(
            workspace,
            selection,
            token=token or None,
        )
    if backend == "modal":
        return await attach_github_repository_to_modal_workspace(
            workspace,
            selection,
            token=token or None,
        )
    raise ValueError(f"Unsupported workspace backend: {backend}")


def is_github_workspace(workspace: WorkspaceRef | dict[str, Any] | None) -> bool:
    if workspace is None:
        return False
    if isinstance(workspace, WorkspaceRef):
        return str(workspace.metadata.get("source") or "").strip().lower() == "github"
    if isinstance(workspace, dict):
        metadata = workspace.get("metadata") or {}
        if not isinstance(metadata, dict):
            return False
        return str(metadata.get("source") or "").strip().lower() == "github"
    return False


def normalize_github_workspace_ref(
    workspace: WorkspaceRef | dict[str, Any] | str | None,
) -> WorkspaceRef:
    """Normalize a workspace reference while preserving GitHub source metadata.

    Thin wrapper around :func:`normalize_workspace_ref` that re-applies the
    GitHub metadata when the caller passes a previously resolved GitHub-backed
    workspace back through the dashboard (for example when refreshing a
    sandbox ID).
    """
    ref = normalize_workspace_ref(workspace, default_locator=DEFAULT_LOCAL_WORKSPACE)
    if not is_github_workspace(workspace) and not is_github_workspace(ref):
        return ref
    if isinstance(workspace, dict):
        original_metadata = workspace.get("metadata") or {}
    elif isinstance(workspace, WorkspaceRef):
        original_metadata = dict(workspace.metadata or {})
    else:
        original_metadata = {}
    if not isinstance(original_metadata, dict):
        original_metadata = {}
    metadata = dict(ref.metadata or {})
    for key, value in original_metadata.items():
        if value in (None, "", []):
            continue
        if key not in metadata or metadata.get(key) in (None, ""):
            metadata[key] = value
    label = str(
        original_metadata.get("repository_full_name") or ref.label or ref.locator
    )
    return WorkspaceRef(
        backend=ref.backend,
        locator=ref.locator,
        label=label,
        metadata=metadata,
    )


__all__ = [
    "GitHubRepositorySelection",
    "attach_github_repository_to_daytona_workspace",
    "attach_github_repository_to_local_workspace",
    "attach_github_repository_to_local_workspace_async",
    "attach_github_repository_to_modal_workspace",
    "attach_github_repository_to_workspace",
    "is_github_workspace",
    "normalize_github_workspace_ref",
]
