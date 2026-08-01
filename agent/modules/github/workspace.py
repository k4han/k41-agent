from __future__ import annotations

import asyncio
import base64
import logging
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent.modules.github.config import get_github_workspace_root
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)

BRANCH_SAFE_RE = re.compile(r"[^A-Za-z0-9._/-]+")
WORKTREE_DIR_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class PreparedWorkspace:
    path: Path
    branch: str
    base_branch: str


class GitHubWorkspaceManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            root if root is not None else get_github_workspace_root()
        ).expanduser()
        self._repo_locks: dict[str, asyncio.Lock] = {}

    def repository_path(self, full_name: str) -> Path:
        owner, repo = _split_full_name(full_name)
        return self.root / owner / repo

    def _lock_for(self, full_name: str) -> asyncio.Lock:
        return self._repo_locks.setdefault(full_name, asyncio.Lock())

    async def ensure_shared_checkout(
        self,
        *,
        full_name: str,
        token: str,
    ) -> Path:
        repo_path = self.repository_path(full_name)
        repo_url = f"https://github.com/{full_name}.git"
        await self._ensure_base_clone(repo_path=repo_path, repo_url=repo_url, token=token)
        return repo_path

    async def _ensure_base_clone(
        self,
        *,
        repo_path: Path,
        repo_url: str,
        token: str,
    ) -> None:
        if repo_path.exists() and not (repo_path / ".git").exists():
            raise ValueError(
                f"Repository workspace exists but is not a Git repository: {repo_path}"
            )

        if not repo_path.exists():
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            await _run_git(
                [
                    "clone",
                    "--origin",
                    "origin",
                    repo_url,
                    str(repo_path),
                ],
                cwd=repo_path.parent,
                token=token,
            )
        else:
            await _run_git(["remote", "set-url", "origin", repo_url], cwd=repo_path)

    async def prepare(
        self,
        *,
        full_name: str,
        default_branch: str,
        branch: str,
        token: str,
    ) -> PreparedWorkspace:
        repo_path = self.repository_path(full_name)
        repo_url = f"https://github.com/{full_name}.git"
        base_branch = default_branch or "main"
        safe_branch = sanitize_branch_name(branch)

        async with self._lock_for(full_name):
            await self._ensure_base_clone(repo_path=repo_path, repo_url=repo_url, token=token)
            await _run_git(["fetch", "--prune", "origin"], cwd=repo_path, token=token)
            worktree_path = self._new_worktree_path(full_name, safe_branch)
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            await _run_git(
                ["worktree", "add", "--detach", str(worktree_path), f"origin/{base_branch}"],
                cwd=repo_path,
            )

        return PreparedWorkspace(path=worktree_path, branch=safe_branch, base_branch=base_branch)

    async def prepare_existing_branch(
        self,
        *,
        full_name: str,
        branch: str,
        base_branch: str,
        token: str,
    ) -> PreparedWorkspace:
        repo_path = self.repository_path(full_name)
        repo_url = f"https://github.com/{full_name}.git"
        branch_name = branch.strip()
        if not branch_name:
            raise ValueError("Branch name is required.")

        async with self._lock_for(full_name):
            await self._ensure_base_clone(repo_path=repo_path, repo_url=repo_url, token=token)
            await _run_git(["fetch", "--prune", "origin"], cwd=repo_path, token=token)
            worktree_path = self._new_worktree_path(full_name, branch_name)
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            await _run_git(
                ["worktree", "add", "--detach", str(worktree_path), f"origin/{branch_name}"],
                cwd=repo_path,
            )

        return PreparedWorkspace(
            path=worktree_path,
            branch=branch_name,
            base_branch=base_branch or "main",
        )

    async def discard_worktree(self, *, full_name: str, path: Path) -> None:
        """Remove a task worktree created by ``prepare``/``prepare_existing_branch``."""
        repo_path = self.repository_path(full_name)
        if not (repo_path / ".git").exists():
            return
        async with self._lock_for(full_name):
            await self._remove_worktree(repo_path, path)

    async def _remove_worktree(self, repo_path: Path, worktree_path: Path) -> None:
        """Remove a worktree, falling back to a direct delete when git fails.

        ``git worktree remove`` can fail for locked or partially-removed
        worktrees; in that case delete the directory directly and prune the
        stale administrative entry from the base repository.
        """
        try:
            await _run_git(
                ["worktree", "remove", "--force", str(worktree_path)],
                cwd=repo_path,
            )
            return
        except Exception as remove_exc:
            logger.warning(
                "git worktree remove failed for %s: %s; deleting directory directly",
                worktree_path,
                remove_exc,
            )
        shutil.rmtree(worktree_path, ignore_errors=True)
        if worktree_path.exists():
            raise RuntimeError(f"Failed to remove worktree directory: {worktree_path}")
        try:
            await _run_git(["worktree", "prune"], cwd=repo_path)
        except Exception as prune_exc:
            logger.warning(
                "Failed to prune worktree metadata in %s: %s",
                repo_path,
                prune_exc,
            )

    async def prune_orphaned_worktrees(self) -> int:
        """Remove worktree directories left behind by interrupted tasks.

        Task worktrees live under ``<root>/<owner>/<repo>.worktrees/*`` and
        are normally cleaned up by the task cleanup hook. If the process is
        restarted mid-task the hook never runs, so on startup we remove any
        leftover worktrees (their tasks were marked failed on restore).
        """
        pruned = 0
        for worktree_path in sorted(self.root.glob("*/*.worktrees/*")):
            if not worktree_path.is_dir():
                continue
            if not _is_task_worktree(worktree_path):
                # Not identifiable as a git worktree: it may be a directory of
                # a repository whose name ends with ".worktrees". Never delete
                # directories we cannot identify as task worktrees.
                continue
            worktrees_dir = worktree_path.parent
            repo_name = worktrees_dir.name[: -len(".worktrees")]
            repo_path = worktrees_dir.parent / repo_name
            full_name = f"{worktrees_dir.parent.name}/{repo_name}"
            try:
                if (repo_path / ".git").exists():
                    async with self._lock_for(full_name):
                        await self._remove_worktree(repo_path, worktree_path)
                else:
                    shutil.rmtree(worktree_path, ignore_errors=True)
                pruned += 1
            except Exception as exc:
                logger.warning(
                    "Failed to prune orphaned worktree %s: %s",
                    worktree_path,
                    exc,
                )
        return pruned

    async def preserve_worktree_changes(
        self,
        *,
        full_name: str,
        path: Path,
        backup_branch: str,
    ) -> None:
        """Save a worktree's commits under ``backup_branch`` before discarding it.

        Used when pushing fails so the agent's work is not lost: any uncommitted
        changes are committed first, then a local branch ref points at the
        resulting commit in the shared repository.
        """
        repo_path = self.repository_path(full_name)
        if not (repo_path / ".git").exists():
            return
        safe_branch = sanitize_branch_name(backup_branch)
        async with self._lock_for(full_name):
            await _run_git(["add", "-A"], cwd=path)
            try:
                await _run_git(
                    [
                        "-c",
                        "user.name=Kai Agent",
                        "-c",
                        "user.email=k41-agent@users.noreply.github.com",
                        "commit",
                        "-m",
                        f"Preserved agent changes from backup branch {safe_branch}",
                    ],
                    cwd=path,
                )
            except Exception as exc:
                logger.warning(
                    "No uncommitted changes to preserve for %s: %s",
                    path,
                    exc,
                )
            try:
                await _run_git(["branch", "-f", safe_branch, "HEAD"], cwd=path)
            except Exception as exc:
                logger.warning(
                    "Failed to create backup branch %s for %s: %s",
                    safe_branch,
                    path,
                    exc,
                )

    def _new_worktree_path(self, full_name: str, branch: str) -> Path:
        owner, repo = _split_full_name(full_name)
        dir_safe_branch = WORKTREE_DIR_SAFE_RE.sub("-", branch).strip("-.") or "task"
        return (
            self.root
            / owner
            / f"{repo}.worktrees"
            / f"{dir_safe_branch}-{uuid.uuid4().hex[:8]}"
        )

    async def has_changes(self, path: Path) -> bool:
        result = await _run_git(["status", "--porcelain"], cwd=path, capture=True)
        return bool(result.strip())

    async def commit_all(self, *, path: Path, message: str) -> None:
        await _run_git(["add", "-A"], cwd=path)
        await _run_git(
            [
                "-c",
                "user.name=Kai Agent",
                "-c",
                "user.email=k41-agent@users.noreply.github.com",
                "commit",
                "-m",
                message,
            ],
            cwd=path,
        )

    async def push_branch(self, *, path: Path, branch: str, token: str) -> None:
        await _run_git(
            ["push", "origin", f"HEAD:{branch}", "--force-with-lease"],
            cwd=path,
            token=token,
        )


def sanitize_branch_name(value: str) -> str:
    branch = BRANCH_SAFE_RE.sub("-", value.strip()).strip("/.")
    branch = re.sub(r"/+", "/", branch)
    return branch or "k41/github-task"


def _split_full_name(full_name: str) -> tuple[str, str]:
    if "/" not in full_name:
        raise ValueError(f"Invalid GitHub repository full name: {full_name}")
    owner, repo = full_name.split("/", 1)
    if not owner or not repo:
        raise ValueError(f"Invalid GitHub repository full name: {full_name}")
    return owner, repo


def _is_task_worktree(path: Path) -> bool:
    """Return True when ``path`` looks like a git worktree checkout.

    A worktree directory contains a ``.git`` file (not a directory) whose
    ``gitdir:`` target points into the base repository's ``.git/worktrees``
    administrative area. This lets startup pruning tell task worktrees apart
    from directories of a repository whose name ends with ``.worktrees``.
    """
    git_pointer = path / ".git"
    if not git_pointer.is_file():
        return False
    try:
        first_line = git_pointer.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return False
    if not first_line.startswith("gitdir:"):
        return False
    target = first_line[len("gitdir:"):].strip().replace("\\", "/")
    return "/.git/worktrees/" in target


async def _run_git(
    args: list[str],
    *,
    cwd: Path,
    token: str | None = None,
    capture: bool = False,
) -> str:
    def run() -> str:
        command = ["git"]
        if token:
            command.extend([
                "-c",
                f"http.https://github.com/.extraHeader={_github_auth_header(token)}",
            ])
        command.extend(args)

        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        if capture:
            return result.stdout
        return ""

    return await asyncio.to_thread(run)


def _github_auth_header(token: str) -> str:
    credentials = base64.b64encode(
        f"x-access-token:{token}".encode("utf-8")
    ).decode("ascii")
    return f"Authorization: Basic {credentials}"


__all__ = [
    "GitHubWorkspaceManager",
    "PreparedWorkspace",
    "sanitize_branch_name",
]
