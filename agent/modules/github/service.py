from __future__ import annotations

import hmac
import json
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
    GitHubSettings,
)
from agent.modules.github.repository import (
    get_github_repository_store,
    load_allowed_skills,
    load_allowed_tools,
    load_mention_triggers,
)
from agent.modules.github.workspace import GitHubWorkspaceManager, sanitize_branch_name
from agent.modules.github.workspace_helpers import (
    prepare_workspace_for_binding,
    remote_commit_all,
    remote_has_changes,
    remote_push_branch,
)
from agent.modules.workspaces import WorkspaceRef, workspace_ref_from_local_path

logger = logging.getLogger(__name__)

SUPPORTED_WEBHOOK_EVENTS = {
    "issues",
    "issue_comment",
    "pull_request_review_comment",
    "installation",
    "installation_repositories",
}
COMPLETION_OPEN_PULL_REQUEST = "open_pull_request"
COMPLETION_UPDATE_PULL_REQUEST = "update_pull_request"


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
    completion_mode: str = COMPLETION_OPEN_PULL_REQUEST
    review_comment_id: int | None = None
    repository_instructions: str = ""
    workspace_ref: WorkspaceRef | None = None


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


def _github_workspace_ref(context: GitHubTaskContext, *, backend: str = "local") -> WorkspaceRef:
    if context.workspace_ref is not None:
        return context.workspace_ref

    if backend == "local":
        return workspace_ref_from_local_path(
            str(context.workspace_path),
            label=context.repository_full_name,
            metadata={
                "source": "github",
                "repository_full_name": context.repository_full_name,
                "branch": context.branch,
                "base_branch": context.base_branch,
                "issue_number": context.issue_number,
                "issue_url": context.issue_url,
                "completion_mode": context.completion_mode,
            },
        )
    return WorkspaceRef(
        backend=backend,
        locator=f"github:{context.repository_full_name}",
        label=context.repository_full_name,
        metadata={
            "source": "github",
            "repository_full_name": context.repository_full_name,
            "branch": context.branch,
            "base_branch": context.base_branch,
            "issue_number": context.issue_number,
            "issue_url": context.issue_url,
            "completion_mode": context.completion_mode,
        },
    )


class GitHubAutomationService:
    def __init__(
        self,
        *,
        client: GitHubAppClient | None = None,
        workspace_manager: GitHubWorkspaceManager | None = None,
    ) -> None:
        self.client = client or GitHubAppClient()
        self.store = get_github_repository_store()
        self.workspace_manager = workspace_manager or GitHubWorkspaceManager()

    @property
    def settings(self) -> GitHubSettings:
        return get_github_settings()

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

    async def get_repository_binding(self, repository_id: int) -> dict[str, Any]:
        binding = await self.store.get_serialized_binding_by_repository_id(repository_id)
        if binding is None:
            raise KeyError(f"GitHub repository '{repository_id}' is not synced.")
        return binding

    async def resolve_repository_workspace(self, repository_id: int) -> dict[str, Any]:
        binding = await self.store.get_binding_by_repository_id(repository_id)
        if binding is None:
            raise KeyError(f"GitHub repository '{repository_id}' is not synced.")

        workspace_backend = getattr(binding, "workspace_backend", "local") or "local"
        if workspace_backend == "local":
            token = await self.client.get_installation_token(int(binding.installation_id))
            path = await self.workspace_manager.ensure_shared_checkout(
                full_name=binding.full_name,
                token=token,
            )
            workspace = workspace_ref_from_local_path(
                str(path.resolve()),
                label=binding.full_name,
                metadata={
                    "source": "github",
                    "repository_full_name": binding.full_name,
                    "default_branch": binding.default_branch,
                },
            )
        else:
            from agent.modules.github.workspace_helpers import _prepare_remote_workspace

            workspace = await _prepare_remote_workspace(
                binding=binding,
                backend=workspace_backend,
                client=self.client,
                token="",
                branch=binding.default_branch or "main",
                default_branch=binding.default_branch,
            )

        return {
            "kind": "github",
            "label": binding.full_name,
            "workspace": workspace.model_dump(),
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
        issue_label_enabled: bool = True,
        issue_comment_enabled: bool = True,
        pr_review_comment_enabled: bool = True,
        repository_instructions: str = "",
        provider_name: str = "",
        model_name: str = "",
        context_trim_threshold: int | None = None,
        tool_policy_mode: str = "inherit",
        allowed_tools: list[str] | None = None,
        allowed_skills: list[str] | None = None,
        branch_prefix: str = "k41",
        workspace_backend: str = "local",
    ) -> dict[str, Any]:
        resolved_agent = resolve_catalog_agent_name(agent_name, self.settings.default_agent, "default")
        normalized_allowed_skills = await _validate_allowed_global_skills(allowed_skills or [])
        return await self.store.update_binding(
            repository_id,
            enabled=enabled,
            agent_name=resolved_agent or "default",
            trigger_label=trigger_label or self.settings.trigger_label,
            mention_triggers=mention_triggers or list(self.settings.mention_triggers),
            notify_platform=notify_platform,
            notify_external_id=notify_external_id,
            notify_channel_id=notify_channel_id,
            issue_label_enabled=issue_label_enabled,
            issue_comment_enabled=issue_comment_enabled,
            pr_review_comment_enabled=pr_review_comment_enabled,
            repository_instructions=repository_instructions,
            provider_name=provider_name,
            model_name=model_name,
            context_trim_threshold=context_trim_threshold,
            tool_policy_mode=tool_policy_mode,
            allowed_tools=allowed_tools or [],
            allowed_skills=normalized_allowed_skills,
            branch_prefix=branch_prefix,
            workspace_backend=workspace_backend,
        )

    async def submit_repository_task(
        self,
        repository_id: int,
        *,
        request: str,
        notify_platform: str = "",
        notify_external_id: str = "",
        notify_channel_id: str = "",
    ) -> str:
        clean_request = request.strip()
        if not clean_request:
            raise ValueError("Request cannot be empty.")

        binding = await self.store.get_binding_by_repository_id(repository_id)
        if binding is None:
            raise KeyError(f"GitHub repository '{repository_id}' is not synced.")

        workspace_payload = await self.resolve_repository_workspace(repository_id)
        workspace = WorkspaceRef(**workspace_payload["workspace"])
        agent_name = resolve_catalog_agent_name(
            binding.agent_name,
            self.settings.default_agent,
            "default",
        ) or "default"
        manager = get_background_task_manager()
        return await manager.submit(
            request=_build_manual_task_prompt(
                request=clean_request,
                repository_full_name=binding.full_name,
                repository_instructions=_repository_instructions(binding),
            ),
            agent_name=agent_name,
            workspace=workspace,
            notify_channel=self._notify_channel(
                notify_platform or getattr(binding, "notify_platform", ""),
                notify_external_id or getattr(binding, "notify_external_id", ""),
                notify_channel_id or getattr(binding, "notify_channel_id", ""),
            ),
            context_trim_threshold=_context_trim_threshold(binding),
            allowed_tool_names=_allowed_tools_for_binding(binding),
            allowed_skill_names=_allowed_skills_for_binding(binding),
            provider=_provider_name(binding),
            model=_model_name(binding),
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

        if event not in SUPPORTED_WEBHOOK_EVENTS:
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

        repository_id = repository.get("id")
        if repository_id is None:
            return {"status": "ignored", "reason": "missing_repository"}

        binding = await self.store.get_binding_by_repository_id(int(repository_id))
        if binding is None or not binding.enabled:
            return {"status": "ignored", "reason": "repository_not_enabled"}

        if event in {"issues", "issue_comment"}:
            if event == "issues" and not getattr(binding, "issue_label_enabled", True):
                return {"status": "ignored", "reason": "issue_label_disabled"}
            if event == "issue_comment" and not getattr(binding, "issue_comment_enabled", True):
                return {"status": "ignored", "reason": "issue_comment_disabled"}

            issue = payload.get("issue") or {}
            if issue.get("pull_request"):
                return {"status": "ignored", "reason": "pull_request_comment"}

            if event == "issues" and not self._should_handle_issue_event(action, issue, binding.trigger_label):
                return {"status": "ignored", "reason": "issue_not_triggered"}

            if event == "issue_comment" and not self._should_handle_comment_event(
                action,
                payload,
                binding.mention_triggers_json,
            ):
                return {"status": "ignored", "reason": "comment_not_triggered"}

            task_id = await self._submit_task(
                payload=payload,
                binding=binding,
                event=event,
                delivery_id=delivery_id,
            )
            return {"status": "submitted", "task_id": task_id}

        if not getattr(binding, "pr_review_comment_enabled", True):
            return {"status": "ignored", "reason": "pr_review_comment_disabled"}

        if not self._should_handle_review_comment_event(action, payload):
            return {"status": "ignored", "reason": "review_comment_not_triggered"}

        pull_request = payload.get("pull_request") or {}
        head = pull_request.get("head") or {}
        if not str(head.get("ref") or ""):
            return {"status": "ignored", "reason": "missing_pr_branch"}
        head_repository = head.get("repo") or {}
        head_repository_full_name = str(head_repository.get("full_name") or binding.full_name)
        if head_repository_full_name != binding.full_name:
            return {"status": "ignored", "reason": "fork_pull_request"}

        task_id = await self._submit_review_comment_task(
            payload=payload,
            binding=binding,
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

    def _should_handle_review_comment_event(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> bool:
        if action != "created":
            return False
        comment = payload.get("comment") or {}
        pull_request = payload.get("pull_request") or {}
        return bool(comment.get("body") and pull_request.get("number"))

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
            f"{_branch_prefix(binding)}/{agent_name}/issue-{issue_number}-"
            f"{(delivery_id or 'manual')[:8]}"
        )

        workspace_backend = getattr(binding, "workspace_backend", "local") or "local"
        workspace = await prepare_workspace_for_binding(
            binding,
            workspace_manager=self.workspace_manager,
            client=self.client,
            settings=self.settings,
            branch=branch,
            default_branch=binding.default_branch or repository.get("default_branch") or "main",
        )

        context = GitHubTaskContext(
            installation_id=installation_id,
            repository_full_name=binding.full_name,
            issue_number=issue_number,
            issue_title=str(issue.get("title") or ""),
            issue_url=str(issue.get("html_url") or ""),
            branch=workspace.metadata.get("branch", branch),
            base_branch=workspace.metadata.get("base_branch", binding.default_branch or "main"),
            workspace_path=workspace.locator if workspace_backend == "local" else Path(workspace.locator),
            repository_instructions=_repository_instructions(binding),
            workspace_ref=workspace,
        )
        prompt = _build_agent_prompt(
            event=event,
            payload=payload,
            context=context,
        )

        manager = get_background_task_manager()
        return await manager.submit(
            request=prompt,
            agent_name=agent_name,
            workspace=_github_workspace_ref(context, backend=workspace_backend),
            notify_channel=self._notify_channel_for_binding(binding),
            completion_hook=lambda task: self.publish_task_result(task, context, workspace_backend=workspace_backend),
            context_trim_threshold=_context_trim_threshold(binding),
            allowed_tool_names=_allowed_tools_for_binding(binding),
            allowed_skill_names=_allowed_skills_for_binding(binding),
            provider=_provider_name(binding),
            model=_model_name(binding),
        )

    async def _submit_review_comment_task(
        self,
        *,
        payload: dict[str, Any],
        binding: Any,
    ) -> str:
        repository = payload["repository"]
        pull_request = payload["pull_request"]
        comment = payload.get("comment") or {}
        installation = payload.get("installation") or {}
        installation_id = int(installation.get("id") or binding.installation_id)
        pull_request_number = int(pull_request["number"])
        head = pull_request.get("head") or {}
        base = pull_request.get("base") or {}
        branch = str(head.get("ref") or "")
        if not branch:
            raise ValueError("Pull request head branch is missing.")

        agent_name = resolve_catalog_agent_name(
            binding.agent_name,
            self.settings.default_agent,
            "default",
        ) or "default"

        workspace_backend = getattr(binding, "workspace_backend", "local") or "local"
        workspace = await prepare_workspace_for_binding(
            binding,
            workspace_manager=self.workspace_manager,
            client=self.client,
            settings=self.settings,
            branch=branch,
            default_branch=str(
                base.get("ref")
                or binding.default_branch
                or repository.get("default_branch")
                or "main"
            ),
            existing_branch=True,
        )

        context = GitHubTaskContext(
            installation_id=installation_id,
            repository_full_name=binding.full_name,
            issue_number=pull_request_number,
            issue_title=str(pull_request.get("title") or ""),
            issue_url=str(pull_request.get("html_url") or ""),
            branch=workspace.metadata.get("branch", branch),
            base_branch=workspace.metadata.get("base_branch", binding.default_branch or "main"),
            workspace_path=workspace.locator if workspace_backend == "local" else Path(workspace.locator),
            completion_mode=COMPLETION_UPDATE_PULL_REQUEST,
            review_comment_id=_optional_int(comment.get("id")),
            repository_instructions=_repository_instructions(binding),
            workspace_ref=workspace,
        )
        prompt = _build_agent_prompt(
            event="pull_request_review_comment",
            payload=payload,
            context=context,
        )

        manager = get_background_task_manager()
        return await manager.submit(
            request=prompt,
            agent_name=agent_name,
            workspace=_github_workspace_ref(context, backend=workspace_backend),
            notify_channel=self._notify_channel_for_binding(binding),
            completion_hook=lambda task: self.publish_task_result(task, context, workspace_backend=workspace_backend),
            context_trim_threshold=_context_trim_threshold(binding),
            allowed_tool_names=_allowed_tools_for_binding(binding),
            allowed_skill_names=_allowed_skills_for_binding(binding),
            provider=_provider_name(binding),
            model=_model_name(binding),
        )

    def _notify_channel_for_binding(self, binding: Any) -> NotifyChannel | None:
        return self._notify_channel(
            getattr(binding, "notify_platform", ""),
            getattr(binding, "notify_external_id", ""),
            getattr(binding, "notify_channel_id", ""),
        )

    def _notify_channel(
        self,
        notify_platform: str,
        notify_external_id: str,
        notify_channel_id: str = "",
    ) -> NotifyChannel | None:
        if not notify_platform or not notify_external_id:
            return None
        return NotifyChannel(
            platform=notify_platform,
            external_id=notify_external_id,
            channel_id=notify_channel_id or notify_external_id,
        )

    async def publish_task_result(
        self,
        task: BackgroundTask,
        context: GitHubTaskContext,
        *,
        workspace_backend: str = "local",
    ) -> None:
        token = await self.client.get_installation_token(context.installation_id)
        completion_mode = getattr(context, "completion_mode", COMPLETION_OPEN_PULL_REQUEST)

        if workspace_backend == "local":
            has_changes = await self.workspace_manager.has_changes(context.workspace_path)
        else:
            ref = _github_workspace_ref(context, backend=workspace_backend)
            has_changes = await remote_has_changes(ref)

        if not has_changes:
            await self._post_completion_comment(
                context,
                body="Kai Agent finished running but did not produce any repository changes.",
            )
            task.result = f"{task.result}\n\nNo repository changes were produced.".strip()
            return

        if completion_mode == COMPLETION_UPDATE_PULL_REQUEST:
            commit_message = f"Address review feedback on PR #{context.issue_number}"
        else:
            commit_message = f"Kai Agent changes for issue #{context.issue_number}"

        if workspace_backend == "local":
            await self.workspace_manager.commit_all(
                path=context.workspace_path,
                message=commit_message,
            )
            await self.workspace_manager.push_branch(
                path=context.workspace_path,
                branch=context.branch,
                token=token,
            )
        else:
            ref = _github_workspace_ref(context, backend=workspace_backend)
            await remote_commit_all(ref, commit_message)
            await remote_push_branch(ref, context.branch, token)

        if completion_mode == COMPLETION_UPDATE_PULL_REQUEST:
            await self._post_completion_comment(
                context,
                body=_review_update_body(task.result),
            )
            task.result = f"{task.result}\n\nPull request updated: {context.issue_url}".strip()
            return

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
                body=f"Kai Agent opened a pull request: {pr_url}",
            )
            task.result = f"{task.result}\n\nPull request: {pr_url}".strip()

    async def _post_completion_comment(
        self,
        context: GitHubTaskContext,
        *,
        body: str,
    ) -> None:
        review_comment_id = getattr(context, "review_comment_id", None)
        completion_mode = getattr(context, "completion_mode", COMPLETION_OPEN_PULL_REQUEST)
        if completion_mode == COMPLETION_UPDATE_PULL_REQUEST and review_comment_id:
            await self.client.create_pull_request_review_comment_reply(
                installation_id=context.installation_id,
                full_name=context.repository_full_name,
                pull_request_number=context.issue_number,
                comment_id=review_comment_id,
                body=body,
            )
            return

        await self.client.create_issue_comment(
            installation_id=context.installation_id,
            full_name=context.repository_full_name,
            issue_number=context.issue_number,
            body=body,
        )


def _build_agent_prompt(
    *,
    event: str,
    payload: dict[str, Any],
    context: GitHubTaskContext,
) -> str:
    if event == "pull_request_review_comment":
        return _build_review_comment_prompt(payload=payload, context=context)

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
    lines.extend([
        "",
        "When you are done, summarize the implementation and any tests you ran.",
    ])
    return "\n".join(lines)


def _build_review_comment_prompt(
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
        "Modify files in the current working directory to address the review feedback.",
        "Do not commit, push, or update the pull request yourself; the backend will do that after you finish.",
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
    lines.extend(
        [
            "",
            "Before editing, inspect the commented file and nearby code in the checkout.",
            "When you are done, summarize the implementation and any tests you ran.",
        ]
    )
    return "\n".join(lines)


def _build_manual_task_prompt(
    *,
    request: str,
    repository_full_name: str,
    repository_instructions: str,
) -> str:
    lines = [
        f"You are working on GitHub repository {repository_full_name}.",
        "Modify files in the current working directory to satisfy the request.",
        "",
        "Request:",
        request,
    ]
    if repository_instructions:
        lines.extend(["", "Repository instructions:", repository_instructions])
    lines.extend(
        [
            "",
            "When you are done, summarize the implementation and any tests you ran.",
        ]
    )
    return "\n".join(lines)


def _pr_title(issue_number: int, issue_title: str) -> str:
    title = issue_title.strip() or "GitHub automation task"
    return f"Fix #{issue_number}: {title}"[:240]


def _pr_body(context: GitHubTaskContext, result: str) -> str:
    summary = result.strip() or "Kai Agent completed the requested changes."
    return (
        f"Automated changes for {context.issue_url}\n\n"
        f"Agent summary:\n\n{summary}\n"
    )


def _review_update_body(result: str) -> str:
    summary = result.strip()
    if not summary:
        return "Kai Agent pushed updates for this review comment."
    return f"Kai Agent pushed updates for this review comment.\n\nAgent summary:\n\n{summary}"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None


def _repository_instructions(binding: Any) -> str:
    return str(getattr(binding, "repository_instructions", "") or "").strip()


def _provider_name(binding: Any) -> str | None:
    value = str(getattr(binding, "provider_name", "") or "").strip()
    return value or None


def _model_name(binding: Any) -> str | None:
    value = str(getattr(binding, "model_name", "") or "").strip()
    return value or None


def _context_trim_threshold(binding: Any) -> int | None:
    raw = getattr(binding, "context_trim_threshold", None)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _allowed_tools_for_binding(binding: Any) -> list[str] | None:
    mode = str(getattr(binding, "tool_policy_mode", "") or "").strip().lower()
    if mode != "custom":
        return None
    tools = load_allowed_tools(str(getattr(binding, "allowed_tools_json", "") or "[]"))
    return tools or None


def _allowed_skills_for_binding(binding: Any) -> list[str]:
    return load_allowed_skills(str(getattr(binding, "allowed_skills_json", "") or "[]"))


async def _validate_allowed_global_skills(values: list[str]) -> list[str]:
    selected = load_allowed_skills(json.dumps(values))
    if not selected:
        return []
    from agent.modules.skills import list_available_skills

    available = {skill.name for skill in list_available_skills()}
    invalid = sorted(name for name in selected if name not in available)
    if invalid:
        raise ValueError(f"Unknown skill(s): {', '.join(invalid)}")
    return selected


def _branch_prefix(binding: Any) -> str:
    prefix = str(getattr(binding, "branch_prefix", "") or "").strip() or "k41"
    safe_prefix = sanitize_branch_name(prefix).strip("/.")
    return safe_prefix or "k41"


_service: GitHubAutomationService | None = None


def get_github_automation_service(request: Any = None) -> GitHubAutomationService:
    if request is not None:
        app = getattr(request, "app", None)
        if app is not None:
            state_service = getattr(app.state, "github_automation_service", None)
            if state_service is not None:
                return state_service

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
