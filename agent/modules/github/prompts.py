"""GitHub task input assembly; automation policy is owned by an agent card."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.modules.agents import get_agent_prompt_template

if TYPE_CHECKING:
    from agent.modules.github.service import GitHubTaskContext


def _automation_policy() -> str:
    return "<automation_policy>\n" + get_agent_prompt_template("github-automation-policy") + "\n</automation_policy>"


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def build_agent_prompt(
    *,
    event: str,
    payload: dict[str, Any],
    context: GitHubTaskContext,
    issue_context: dict[str, Any] | None = None,
) -> str:
    if event == "pull_request_review_comment":
        return build_review_comment_prompt(payload=payload, context=context)

    issue = payload["issue"]
    comment = payload.get("comment") or {}
    comment_body = str(comment.get("body") or "").strip()
    issue_body = str(issue.get("body") or "").strip()

    lines = [
        f"You are working on GitHub repository {context.repository_full_name}.",
        f"The local checkout is already on branch {context.branch}.",
        _automation_policy(),
        "",
        f"Issue: #{context.issue_number} {context.issue_title}",
        f"Issue URL: {context.issue_url}",
        "",
        "Issue body:",
        issue_body or "(empty)",
    ]

    if issue_context:
        linked_prs = issue_context.get("linked_pull_requests") or []
        if linked_prs:
            lines.extend(["", "Existing pull requests linked to this issue:"])
            for pr in linked_prs:
                pr_state = pr.get("state", "unknown")
                pr_number = pr.get("number", "?")
                pr_title = pr.get("title", "")
                pr_url = pr.get("url", "")
                pr_branch = pr.get("head_branch", "")
                lines.append(
                    f"- PR #{pr_number} [{pr_state}]: {pr_title} "
                    f"(branch: {pr_branch}) {pr_url}"
                )

        comments = issue_context.get("comments") or []
        if comments:
            recent_comments = comments[-10:]
            lines.extend(["", "Recent issue comments:"])
            for c in recent_comments:
                author = c.get("author", "unknown")
                body = c.get("body", "")
                if body:
                    lines.append(f"- @{author}: {body[:500]}")

    if context.repository_instructions:
        lines.extend(
            [
                "",
                "Repository instructions:",
                context.repository_instructions,
            ]
        )
    if event == "issue_comment" and comment_body:
        lines.extend(["", "Trigger comment:", comment_body])
    return "\n".join(lines)


def build_review_comment_prompt(
    *,
    payload: dict[str, Any],
    context: GitHubTaskContext,
) -> str:
    comment = payload.get("comment") or {}
    comment_body = str(comment.get("body") or "").strip()
    comment_path = str(comment.get("path") or "").strip()
    comment_line = _optional_int(comment.get("line")) or _optional_int(
        comment.get("original_line")
    )
    diff_hunk = str(comment.get("diff_hunk") or "").strip()

    lines = [
        f"You are working on GitHub repository {context.repository_full_name}.",
        f"The local checkout is already on pull request branch {context.branch}.",
        _automation_policy(),
        "",
        f"Pull request: #{context.issue_number} {context.issue_title}",
        f"Pull request URL: {context.issue_url}",
        "",
        "Review comment:",
        comment_body or "(empty)",
        "",
        "Comment location:",
        f"File: {comment_path or '(unknown)'}",
        f"Line: {comment_line if comment_line is not None else '(unknown)'}",
    ]
    if diff_hunk:
        lines.extend(["", "Diff hunk:", diff_hunk])
    if context.repository_instructions:
        lines.extend(
            [
                "",
                "Repository instructions:",
                context.repository_instructions,
            ]
        )
    return "\n".join(lines)


def build_manual_task_prompt(
    *,
    request: str,
    repository_full_name: str,
    repository_instructions: str,
) -> str:
    lines = [
        f"You are working on GitHub repository {repository_full_name}.",
        "",
        "Request:",
        request,
    ]
    if repository_instructions:
        lines.extend(["", "Repository instructions:", repository_instructions])
    return "\n".join(lines)


