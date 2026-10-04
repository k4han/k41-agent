"""GitHub tools for the agent to inspect issues and pull requests.

These tools use the GitHub App installation token stored in the project, so
they work for private repositories as long as the app is installed.
"""

import logging
from typing import Any

import httpx
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from agent.modules.github import GitHubAppClient, get_github_repository_store
from agent.modules.tools.builtin.workspace import get_workspace
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode

logger = logging.getLogger(__name__)



class GitHubIssuePullRequestsInput(BaseModel):
    issue_number: int = Field(
        description="GitHub issue number to look up linked pull requests.",
    )


class GitHubPullRequestInput(BaseModel):
    pull_request_number: int = Field(
        description="GitHub pull request number to inspect.",
    )


def _github_repo_context(runtime: ToolRuntime[Any, Any]) -> tuple[str, int]:
    workspace = get_workspace(runtime)
    metadata = workspace.metadata or {}
    if metadata.get("source") != "github":
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "Current workspace is not a GitHub repository checkout.",
        )
    full_name = str(metadata.get("repository_full_name") or "").strip()
    installation_id = metadata.get("installation_id")
    if not full_name:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "GitHub repository full name is missing from workspace metadata.",
        )
    if installation_id:
        return full_name, int(installation_id)

    # Fall back to 0; callers should resolve via the repository store when needed.
    return full_name, 0


async def _resolve_installation_id(full_name: str) -> int:
    store = get_github_repository_store()
    binding = await store.get_binding_by_full_name(full_name)
    if binding is None:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            f"GitHub repository '{full_name}' is not synced or not enabled.",
        )
    return int(binding["installation_id"])


async def _github_client_for_repo(
    runtime: ToolRuntime[Any, Any],
) -> tuple[GitHubAppClient, str, int]:
    full_name, installation_id = _github_repo_context(runtime)
    if installation_id <= 0:
        installation_id = await _resolve_installation_id(full_name)
    client = GitHubAppClient()
    return client, full_name, installation_id


def _format_pr_summary(pr: dict[str, Any]) -> str:
    number = pr.get("number", "?")
    title = str(pr.get("title") or "")
    state = str(pr.get("state") or "unknown")
    head = pr.get("head") or {}
    branch = str(head.get("ref") or "")
    url = str(pr.get("html_url") or "")
    return f"- PR #{number} [{state}]: {title} (branch: {branch}) {url}"


def _format_pr_detail(pr: dict[str, Any]) -> str:
    lines = [
        f"Pull Request #{pr.get('number', '?')}: {pr.get('title', '')}",
        f"State: {pr.get('state', 'unknown')}",
        f"URL: {pr.get('html_url', '')}",
        f"Author: {pr.get('user', {}).get('login', '')}",
        f"Draft: {pr.get('draft', False)}",
        f"Head: {pr.get('head', {}).get('ref', '')} -> Base: {pr.get('base', {}).get('ref', '')}",
    ]
    body = str(pr.get("body") or "").strip()
    if body:
        lines.extend(["", "Body:", body])
    return "\n".join(lines)


@register_tool(
    category=ToolCategory.WEB,
    capabilities=[ToolCapability.NETWORK, ToolCapability.REQUIRES_WORKSPACE],
    tags=["github", "api"],
)
@tool(args_schema=GitHubIssuePullRequestsInput)
async def github_list_issue_pull_requests(
    issue_number: int,
    runtime: ToolRuntime[Any, Any],
) -> str:
    """List pull requests linked to a GitHub issue.

    Works for private repositories because the call uses the GitHub App
    installation token for the current repository.

    Args:
        issue_number: The GitHub issue number to look up.
    """
    client, full_name, installation_id = await _github_client_for_repo(runtime)
    try:
        prs = await client.list_pull_requests_for_issue(
            installation_id=installation_id,
            full_name=full_name,
            issue_number=issue_number,
        )
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"GitHub API returned HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except httpx.RequestError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"GitHub API request failed: {exc}",
        ) from exc

    if not prs:
        return f"No linked pull requests found for issue #{issue_number} in {full_name}."

    lines = [
        f"Linked pull requests for issue #{issue_number} in {full_name}:",
        "",
    ]
    lines.extend(_format_pr_summary(pr) for pr in prs)
    return "\n".join(lines)


@register_tool(
    category=ToolCategory.WEB,
    capabilities=[ToolCapability.NETWORK, ToolCapability.REQUIRES_WORKSPACE],
    tags=["github", "api"],
)
@tool(args_schema=GitHubPullRequestInput)
async def github_get_pull_request(
    pull_request_number: int,
    runtime: ToolRuntime[Any, Any],
) -> str:
    """Get details of a GitHub pull request.

    Works for private repositories because the call uses the GitHub App
    installation token for the current repository.

    Args:
        pull_request_number: The GitHub pull request number.
    """
    client, full_name, installation_id = await _github_client_for_repo(runtime)
    try:
        pr = await client.get_pull_request(
            installation_id=installation_id,
            full_name=full_name,
            pull_request_number=pull_request_number,
        )
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"GitHub API returned HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except httpx.RequestError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"GitHub API request failed: {exc}",
        ) from exc

    return _format_pr_detail(pr)


@register_tool(
    category=ToolCategory.WEB,
    capabilities=[ToolCapability.NETWORK, ToolCapability.REQUIRES_WORKSPACE],
    tags=["github", "api"],
)
@tool(args_schema=GitHubPullRequestInput)
async def github_get_pull_request_diff(
    pull_request_number: int,
    runtime: ToolRuntime[Any, Any],
) -> str:
    """Get the diff of a GitHub pull request.

    Works for private repositories because the call uses the GitHub App
    installation token for the current repository.

    Args:
        pull_request_number: The GitHub pull request number.
    """
    client, full_name, installation_id = await _github_client_for_repo(runtime)
    try:
        diff = await client.get_pull_request_diff(
            installation_id=installation_id,
            full_name=full_name,
            pull_request_number=pull_request_number,
        )
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"GitHub API returned HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except httpx.RequestError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"GitHub API request failed: {exc}",
        ) from exc

    return f"Diff for PR #{pull_request_number} in {full_name}:\n\n{diff}"


__all__ = [
    "github_get_pull_request",
    "github_get_pull_request_diff",
    "github_list_issue_pull_requests",
]
