"""Helpers for preparing GitHub workspaces across different backends.

This module provides unified workspace preparation for GitHub automation,
supporting local, Daytona, and Modal backends. It also provides remote
git operations (commit, push) for non-local backends.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent.modules.workspaces import WorkspaceRef, workspace_ref_from_local_path

if TYPE_CHECKING:
    from agent.modules.github.client import GitHubAppClient
    from agent.modules.github.config import GitHubSettings
    from agent.modules.github.models import GitHubRepositoryBinding
    from agent.modules.github.workspace import GitHubWorkspaceManager

logger = logging.getLogger(__name__)


async def prepare_workspace_for_binding(
    binding: GitHubRepositoryBinding,
    *,
    workspace_manager: GitHubWorkspaceManager,
    client: GitHubAppClient,
    settings: GitHubSettings,
    branch: str,
    default_branch: str | None = None,
    existing_branch: bool = False,
) -> WorkspaceRef:
    """Prepare a workspace based on the binding's backend choice.

    For local backspaces, uses the existing GitHubWorkspaceManager to clone
    to the local filesystem. For Daytona/Modal backends, uses the
    github_clone adapter to clone inside the sandbox.

    When ``existing_branch`` is True, the workspace is prepared by checking
    out the existing branch (for review comment tasks). Otherwise, a new
    branch is created from the default branch (for issue tasks).

    Returns a WorkspaceRef ready to be passed to BackgroundTaskManager.
    """
    backend = getattr(binding, "workspace_backend", "local") or "local"
    from agent.modules.workspaces import get_workspace_backend_registry

    if backend not in get_workspace_backend_registry().names():
        backend = "local"

    installation_id = int(getattr(binding, "installation_id", 0) or 0)
    token = await client.get_installation_token(installation_id)

    if backend == "local":
        return await _prepare_local_workspace(
            binding=binding,
            workspace_manager=workspace_manager,
            token=token,
            branch=branch,
            default_branch=default_branch,
            existing_branch=existing_branch,
        )

    return await _prepare_remote_workspace(
        binding=binding,
        backend=backend,
        client=client,
        token=token,
        branch=branch,
        default_branch=default_branch,
    )


async def _prepare_local_workspace(
    *,
    binding: GitHubRepositoryBinding,
    workspace_manager: GitHubWorkspaceManager,
    token: str,
    branch: str,
    default_branch: str | None = None,
    existing_branch: bool = False,
) -> WorkspaceRef:
    """Clone repository to local filesystem and return WorkspaceRef."""
    if existing_branch:
        prepared = await workspace_manager.prepare_existing_branch(
            full_name=binding.full_name,
            branch=branch,
            base_branch=default_branch or binding.default_branch or "main",
            token=token,
        )
    else:
        prepared = await workspace_manager.prepare(
            full_name=binding.full_name,
            default_branch=default_branch or binding.default_branch or "main",
            branch=branch,
            token=token,
        )
    return workspace_ref_from_local_path(
        str(prepared.path),
        label=binding.full_name,
        metadata={
            "source": "github",
            "repository_full_name": binding.full_name,
            "branch": prepared.branch,
            "base_branch": prepared.base_branch,
        },
    )


async def _prepare_remote_workspace(
    *,
    binding: GitHubRepositoryBinding,
    backend: str,
    client: GitHubAppClient,
    token: str,
    branch: str,
    default_branch: str | None = None,
) -> WorkspaceRef:
    """Clone repository inside a Daytona/Modal sandbox and return WorkspaceRef."""
    from agent.modules.workspaces import (
        GitHubRepositorySelection,
        attach_github_repository_to_daytona_workspace,
        attach_github_repository_to_modal_workspace,
    )

    selection = GitHubRepositorySelection(
        repository_id=int(getattr(binding, "repository_id", 0) or 0),
        full_name=binding.full_name,
        default_branch=default_branch or binding.default_branch or "main",
        installation_id=int(getattr(binding, "installation_id", 0) or 0),
        token=token,
    )

    if backend == "daytona":
        workspace_ref = await _create_daytona_workspace(binding.full_name)
        ref = await attach_github_repository_to_daytona_workspace(
            workspace_ref,
            selection,
            token=token,
        )
    elif backend == "modal":
        workspace_ref = await _create_modal_workspace(binding.full_name)
        ref = await attach_github_repository_to_modal_workspace(
            workspace_ref,
            selection,
            token=token,
        )
    elif backend == "openshell":
        workspace_ref = await _create_open_shell_workspace(binding.full_name)
        ref = await attach_github_repository_to_workspace(
            workspace_ref,
            repository_id=selection.repository_id,
            install_token=token,
        )
    else:
        raise ValueError(f"Unsupported remote backend: {backend}")

    metadata = dict(ref.metadata or {})
    metadata["branch"] = branch
    metadata["base_branch"] = default_branch or binding.default_branch or "main"
    return WorkspaceRef(
        backend=ref.backend,
        locator=ref.locator,
        label=ref.label,
        metadata=metadata,
    )


async def _create_daytona_workspace(label: str) -> WorkspaceRef:
    """Create a new Daytona sandbox and return its WorkspaceRef."""
    from agent.modules.workspaces import DAYTONA_BACKEND, create_workspace_backend

    return await create_workspace_backend(DAYTONA_BACKEND, label=label)


async def _create_modal_workspace(label: str) -> WorkspaceRef:
    """Create a new Modal sandbox and return its WorkspaceRef."""
    from agent.modules.workspaces import MODAL_BACKEND, create_workspace_backend

    return await create_workspace_backend(MODAL_BACKEND, label=label)


async def _create_open_shell_workspace(label: str) -> WorkspaceRef:
    """Create a new OpenShell sandbox and return its WorkspaceRef."""
    from agent.modules.workspaces import OPEN_SHELL_BACKEND, create_workspace_backend

    return await create_workspace_backend(OPEN_SHELL_BACKEND, label=label)


async def remote_has_changes(ref: WorkspaceRef) -> bool:
    """Check if there are uncommitted changes in a remote workspace."""
    from agent.modules.workspaces import get_workspace_change_inspector

    inspector = await get_workspace_change_inspector(ref)
    changes = await inspector.changes()
    return bool(changes.get("changes"))


async def remote_commit_all(ref: WorkspaceRef, message: str) -> None:
    """Stage all changes and commit in a remote workspace."""
    from agent.modules.workspaces import get_workspace_command_executor

    executor = await get_workspace_command_executor(ref)
    await executor.execute("git config user.email 'bot@k41-agent.local'")
    await executor.execute("git config user.name 'Kai Agent'")
    await executor.execute("git add -A")
    result = await executor.execute(f"git commit -m '{_escape_shell(message)}'")
    if result.exit_code != 0 and "nothing to commit" not in (result.output or ""):
        logger.warning("Git commit returned non-zero: %s", result.output)


async def remote_push_branch(
    ref: WorkspaceRef,
    branch: str,
    token: str,
) -> None:
    """Push branch to remote in a remote workspace."""
    from agent.modules.workspaces import get_workspace_command_executor

    executor = await get_workspace_command_executor(ref)
    repo_url = _build_push_url(ref, token)
    await executor.execute(f"git remote set-url origin {repo_url}")
    result = await executor.execute(
        f"git push origin HEAD:{branch} --force-with-lease"
    )
    if result.exit_code != 0:
        logger.warning("Git push returned non-zero: %s", result.output)


def _build_push_url(ref: WorkspaceRef, token: str) -> str:
    """Build a GitHub push URL with embedded token."""
    repo_full_name = str(
        ref.metadata.get("repository_full_name") or ""
    ).strip()
    if not repo_full_name:
        raise ValueError("Repository full name not found in workspace metadata")
    return f"https://x-access-token:{token}@github.com/{repo_full_name}.git"


def _escape_shell(value: str) -> str:
    """Escape single quotes for shell commands."""
    return value.replace("'", "'\\''")


__all__ = [
    "prepare_workspace_for_binding",
    "remote_has_changes",
    "remote_commit_all",
    "remote_push_branch",
]
