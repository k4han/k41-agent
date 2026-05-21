from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from agent.modules.agent_runtime import BackgroundTask, NotifyChannel, get_background_task_manager
from agent.modules.agents import resolve_catalog_agent_name
from agent.modules.github.client import GitHubAppClient
from agent.modules.github.config import (
    DEFAULT_TRIGGER_LABEL,
    get_github_settings,
)
from agent.modules.github.repository import get_github_repository_store, load_mention_triggers
from agent.modules.github.workspace import GitHubWorkspaceManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GitHubTaskContext:
    installation_id: int
    repository_full_name: str
    issue_number: int
    issue_title: str
    issue_url: str
    branch: str
    base_branch: str
    workspace_path: Path


def verify_webhook_signature(
    *,
    secret: str,
    body: bytes,
    signature_header: str | None,
) -> bool:
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


class GitHubAutomationService:
    def __init__(
        self,
        *,
        client: GitHubAppClient | None = None,
        workspace_manager: GitHubWorkspaceManager | None = None,
    ) -> None:
        self.settings = get_github_settings()
        self.client = client or GitHubAppClient(self.settings)
        self.store = get_github_repository_store()
        self.workspace_manager = workspace_manager or GitHubWorkspaceManager()

    async def sync_installations(self) -> dict[str, int]:
        if not self.settings.is_configured:
            raise ValueError("GitHub App is not configured.")

        installations = await self.client.list_installations()
        repo_count = 0
        for installation in installations:
            await self.store.upsert_installation(installation)
            installation_id = int(installation["id"])
            repositories = await self.client.list_installation_repositories(installation_id)
            for repository in repositories:
                await self.store.upsert_repository(
                    repository,
                    installation_id=installation_id,
                    default_agent=self.settings.default_agent,
                    default_trigger_label=self.settings.trigger_label,
                    default_mention_triggers=self.settings.mention_triggers,
                )
                repo_count += 1

        return {"installations": len(installations), "repositories": repo_count}

    async def list_repository_bindings(self) -> list[dict[str, Any]]:
        return await self.store.list_bindings()

    async def resolve_repository_workspace(self, repository_id: int) -> dict[str, str]:
        binding = await self.store.get_binding_by_repository_id(repository_id)
        if binding is None:
            raise KeyError(f"GitHub repository '{repository_id}' is not synced.")

        token = await self.client.get_installation_token(int(binding.installation_id))
        path = await self.workspace_manager.ensure_shared_checkout(
            full_name=binding.full_name,
            token=token,
        )
        return {
            "kind": "github",
            "label": binding.full_name,
            "working_dir": str(path.resolve()),
        }

    async def update_repository_binding(
        self,
        repository_id: int,
        *,
        enabled: bool,
        agent_name: str,
        trigger_label: str,
        mention_triggers: list[str],
        notify_platform: str = "",
        notify_external_id: str = "",
        notify_channel_id: str = "",
    ) -> dict[str, Any]:
        resolved_agent = resolve_catalog_agent_name(agent_name, self.settings.default_agent, "default")
        return await self.store.update_binding(
            repository_id,
            enabled=enabled,
            agent_name=resolved_agent or "default",
            trigger_label=trigger_label or self.settings.trigger_label,
            mention_triggers=mention_triggers or list(self.settings.mention_triggers),
            notify_platform=notify_platform,
            notify_external_id=notify_external_id,
            notify_channel_id=notify_channel_id,
        )

    async def handle_webhook(
        self,
        *,
        event: str,
        delivery_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if event == "ping":
            return {"status": "ok", "event": "ping"}

        if event not in {"issues", "issue_comment", "installation", "installation_repositories"}:
            return {"status": "ignored", "reason": "unsupported_event"}

        action = str(payload.get("action") or "")
        repository = payload.get("repository") or {}
        repository_full_name = str(repository.get("full_name") or "")

        if delivery_id:
            first_seen = await self.store.mark_delivery_seen(
                delivery_id,
                event=event,
                action=action,
                repository_full_name=repository_full_name,
            )
            if not first_seen:
                return {"status": "ignored", "reason": "duplicate_delivery"}

        if event in {"installation", "installation_repositories"}:
            if self.settings.is_configured:
                try:
                    sync_result = await self.sync_installations()
                    return {"status": "synced", **sync_result}
                except Exception as exc:
                    logger.warning("GitHub installation sync from webhook failed: %s", exc)
            return {"status": "ignored", "reason": "installation_event"}

        sender = payload.get("sender") or {}
        if str(sender.get("type") or "").lower() == "bot":
            return {"status": "ignored", "reason": "bot_sender"}

        issue = payload.get("issue") or {}
        if issue.get("pull_request"):
            return {"status": "ignored", "reason": "pull_request_comment"}

        repository_id = repository.get("id")
        if repository_id is None:
            return {"status": "ignored", "reason": "missing_repository"}

        binding = await self.store.get_binding_by_repository_id(int(repository_id))
        if binding is None or not binding.enabled:
            return {"status": "ignored", "reason": "repository_not_enabled"}

        if event == "issues" and not self._should_handle_issue_event(action, issue, binding.trigger_label):
            return {"status": "ignored", "reason": "issue_not_triggered"}

        if event == "issue_comment" and not self._should_handle_comment_event(action, payload, binding.mention_triggers_json):
            return {"status": "ignored", "reason": "comment_not_triggered"}

        task_id = await self._submit_task(
            payload=payload,
            binding=binding,
            event=event,
            delivery_id=delivery_id,
        )
        return {"status": "submitted", "task_id": task_id}

    def _should_handle_issue_event(self, action: str, issue: dict[str, Any], trigger_label: str) -> bool:
        if action not in {"opened", "reopened", "labeled"}:
            return False
        label = (trigger_label or self.settings.trigger_label or DEFAULT_TRIGGER_LABEL).lower()
        labels = issue.get("labels") or []
        return any(str(item.get("name") or "").lower() == label for item in labels)

    def _should_handle_comment_event(
        self,
        action: str,
        payload: dict[str, Any],
        mention_triggers_json: str,
    ) -> bool:
        if action != "created":
            return False
        body = str((payload.get("comment") or {}).get("body") or "").lower()
        triggers = load_mention_triggers(mention_triggers_json)
        return any(trigger.lower() in body for trigger in triggers)

    async def _submit_task(
        self,
        *,
        payload: dict[str, Any],
        binding: Any,
        event: str,
        delivery_id: str,
    ) -> str:
        repository = payload["repository"]
        issue = payload["issue"]
        installation = payload.get("installation") or {}
        installation_id = int(installation.get("id") or binding.installation_id)
        issue_number = int(issue["number"])
        agent_name = resolve_catalog_agent_name(
            binding.agent_name,
            self.settings.default_agent,
            "default",
        ) or "default"
        branch = (
            f"kaka/{agent_name}/issue-{issue_number}-"
            f"{(delivery_id or 'manual')[:8]}"
        )

        token = await self.client.get_installation_token(installation_id)
        prepared = await self.workspace_manager.prepare(
            full_name=binding.full_name,
            default_branch=binding.default_branch or repository.get("default_branch") or "main",
            branch=branch,
            token=token,
        )

        context = GitHubTaskContext(
            installation_id=installation_id,
            repository_full_name=binding.full_name,
            issue_number=issue_number,
            issue_title=str(issue.get("title") or ""),
            issue_url=str(issue.get("html_url") or ""),
            branch=prepared.branch,
            base_branch=prepared.base_branch,
            workspace_path=prepared.path,
        )
        prompt = _build_agent_prompt(
            event=event,
            payload=payload,
            context=context,
        )

        manager = get_background_task_manager()
        notify_channel = None
        if binding.notify_platform and binding.notify_external_id:
            notify_channel = NotifyChannel(
                platform=binding.notify_platform,
                external_id=binding.notify_external_id,
                channel_id=binding.notify_channel_id or binding.notify_external_id,
            )
        return await manager.submit(
            request=prompt,
            agent_name=agent_name,
            working_dir=str(prepared.path),
            notify_channel=notify_channel,
            completion_hook=lambda task: self.publish_task_result(task, context),
        )

    async def publish_task_result(
        self,
        task: BackgroundTask,
        context: GitHubTaskContext,
    ) -> None:
        token = await self.client.get_installation_token(context.installation_id)
        if not await self.workspace_manager.has_changes(context.workspace_path):
            await self.client.create_issue_comment(
                installation_id=context.installation_id,
                full_name=context.repository_full_name,
                issue_number=context.issue_number,
                body=(
                    "Kaka Agent finished running but did not produce any repository changes."
                ),
            )
            task.result = f"{task.result}\n\nNo repository changes were produced.".strip()
            return

        commit_message = f"Kaka Agent changes for issue #{context.issue_number}"
        await self.workspace_manager.commit_all(
            path=context.workspace_path,
            message=commit_message,
        )
        await self.workspace_manager.push_branch(
            path=context.workspace_path,
            branch=context.branch,
            token=token,
        )

        pr = await self.client.create_pull_request(
            installation_id=context.installation_id,
            full_name=context.repository_full_name,
            title=_pr_title(context.issue_number, context.issue_title),
            head=context.branch,
            base=context.base_branch,
            body=_pr_body(context, task.result),
        )
        pr_url = str(pr.get("html_url") or "")
        if pr_url:
            await self.client.create_issue_comment(
                installation_id=context.installation_id,
                full_name=context.repository_full_name,
                issue_number=context.issue_number,
                body=f"Kaka Agent opened a pull request: {pr_url}",
            )
            task.result = f"{task.result}\n\nPull request: {pr_url}".strip()


def _build_agent_prompt(
    *,
    event: str,
    payload: dict[str, Any],
    context: GitHubTaskContext,
) -> str:
    issue = payload["issue"]
    comment = payload.get("comment") or {}
    comment_body = str(comment.get("body") or "").strip()
    issue_body = str(issue.get("body") or "").strip()

    lines = [
        f"You are working on GitHub repository {context.repository_full_name}.",
        f"The local checkout is already on branch {context.branch}.",
        "Modify files in the current working directory to address the issue.",
        "Do not commit, push, or open the pull request yourself; the backend will do that after you finish.",
        "",
        f"Issue: #{context.issue_number} {context.issue_title}",
        f"Issue URL: {context.issue_url}",
        "",
        "Issue body:",
        issue_body or "(empty)",
    ]
    if event == "issue_comment" and comment_body:
        lines.extend(["", "Trigger comment:", comment_body])
    lines.extend([
        "",
        "When you are done, summarize the implementation and any tests you ran.",
    ])
    return "\n".join(lines)


def _pr_title(issue_number: int, issue_title: str) -> str:
    title = issue_title.strip() or "GitHub automation task"
    return f"Fix #{issue_number}: {title}"[:240]


def _pr_body(context: GitHubTaskContext, result: str) -> str:
    summary = result.strip() or "Kaka Agent completed the requested changes."
    return (
        f"Automated changes for {context.issue_url}\n\n"
        f"Agent summary:\n\n{summary}\n"
    )


_service: GitHubAutomationService | None = None


def get_github_automation_service() -> GitHubAutomationService:
    global _service
    if _service is None:
        _service = GitHubAutomationService()
    return _service


__all__ = [
    "GitHubAutomationService",
    "GitHubTaskContext",
    "get_github_automation_service",
    "verify_webhook_signature",
]
