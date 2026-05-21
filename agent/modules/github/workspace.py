from __future__ import annotations

import asyncio
import base64
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent.modules.github.config import GITHUB_WORKSPACE_ROOT

BRANCH_SAFE_RE = re.compile(r"[^A-Za-z0-9._/-]+")


@dataclass(frozen=True, slots=True)
class PreparedWorkspace:
    path: Path
    branch: str
    base_branch: str


class GitHubWorkspaceManager:
    def __init__(self, root: Path = GITHUB_WORKSPACE_ROOT) -> None:
        self.root = root.expanduser()

    def repository_path(self, full_name: str) -> Path:
        owner, repo = _split_full_name(full_name)
        return self.root / owner / repo

    async def ensure_shared_checkout(
        self,
        *,
        full_name: str,
        token: str,
    ) -> Path:
        repo_path = self.repository_path(full_name)
        repo_url = f"https://github.com/{full_name}.git"

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

        return repo_path

    async def prepare(
        self,
        *,
        full_name: str,
        default_branch: str,
        branch: str,
        token: str,
    ) -> PreparedWorkspace:
        owner, repo = _split_full_name(full_name)
        repo_path = self.root / owner / repo
        repo_url = f"https://github.com/{full_name}.git"
        base_branch = default_branch or "main"
        safe_branch = sanitize_branch_name(branch)

        if not (repo_path / ".git").exists():
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

        await _run_git(["fetch", "--prune", "origin"], cwd=repo_path, token=token)
        await _run_git(["checkout", "-B", safe_branch, f"origin/{base_branch}"], cwd=repo_path)
        await _run_git(["clean", "-fd"], cwd=repo_path)

        return PreparedWorkspace(path=repo_path, branch=safe_branch, base_branch=base_branch)

    async def has_changes(self, path: Path) -> bool:
        result = await _run_git(["status", "--porcelain"], cwd=path, capture=True)
        return bool(result.strip())

    async def commit_all(self, *, path: Path, message: str) -> None:
        await _run_git(["add", "-A"], cwd=path)
        await _run_git(
            [
                "-c",
                "user.name=Kaka Agent",
                "-c",
                "user.email=kaka-agent@users.noreply.github.com",
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
    return branch or "kaka/github-task"


def _split_full_name(full_name: str) -> tuple[str, str]:
    if "/" not in full_name:
        raise ValueError(f"Invalid GitHub repository full name: {full_name}")
    owner, repo = full_name.split("/", 1)
    if not owner or not repo:
        raise ValueError(f"Invalid GitHub repository full name: {full_name}")
    return owner, repo


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
