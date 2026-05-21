from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.modules.agent_runtime.background_tasks import BackgroundTask
from agent.modules.github.service import GitHubAutomationService, verify_webhook_signature
from agent.modules.github.workspace import PreparedWorkspace, GitHubWorkspaceManager, sanitize_branch_name


class FakeClient:
    def __init__(self) -> None:
        self.comments: list[dict] = []
        self.pull_requests: list[dict] = []
        self.review_comment_replies: list[dict] = []
        self.installations = [
            {"id": 10, "account": {"login": "octo", "type": "Organization"}}
        ]
        self.repositories = [
            {
                "id": 100,
                "full_name": "octo/example",
                "private": False,
                "default_branch": "main",
                "owner": {"login": "octo"},
            }
        ]

    async def get_installation_token(self, installation_id: int) -> str:
        return f"token-{installation_id}"

    async def list_installations(self):
        return self.installations

    async def list_installation_repositories(self, installation_id: int):
        return self.repositories

    async def create_issue_comment(self, **kwargs):
        self.comments.append(kwargs)
        return {"html_url": "https://github.com/octo/example/issues/1#comment"}

    async def create_pull_request(self, **kwargs):
        self.pull_requests.append(kwargs)
        return {"html_url": "https://github.com/octo/example/pull/2"}

    async def create_pull_request_review_comment_reply(self, **kwargs):
        self.review_comment_replies.append(kwargs)
        return {"html_url": "https://github.com/octo/example/pull/2#discussion_r123"}


class FakeStore:
    def __init__(self, binding=None, first_seen: bool = True) -> None:
        self.binding = binding
        self.first_seen = first_seen
        self.installations = []
        self.repositories = []

    async def mark_delivery_seen(self, *args, **kwargs) -> bool:
        return self.first_seen

    async def get_binding_by_repository_id(self, repository_id: int):
        return self.binding

    async def upsert_installation(self, installation):
        self.installations.append(installation)

    async def upsert_repository(self, repository, **kwargs):
        self.repositories.append((repository, kwargs))
        return {"repository_id": repository["id"]}


class FakeWorkspace:
    def __init__(self, tmp_path: Path, has_changes: bool = True) -> None:
        self.tmp_path = tmp_path
        self.has_changes_value = has_changes
        self.prepared = []
        self.commits = []
        self.pushes = []

    async def prepare(self, **kwargs):
        self.prepared.append(kwargs)
        return PreparedWorkspace(
            path=self.tmp_path,
            branch=sanitize_branch_name(kwargs["branch"]),
            base_branch=kwargs["default_branch"],
        )

    async def prepare_existing_branch(self, **kwargs):
        self.prepared.append(kwargs)
        return PreparedWorkspace(
            path=self.tmp_path,
            branch=kwargs["branch"],
            base_branch=kwargs["base_branch"],
        )

    async def has_changes(self, path: Path) -> bool:
        return self.has_changes_value

    async def commit_all(self, **kwargs) -> None:
        self.commits.append(kwargs)

    async def push_branch(self, **kwargs) -> None:
        self.pushes.append(kwargs)


class FakeTaskManager:
    def __init__(self) -> None:
        self.submissions = []

    async def submit(self, **kwargs) -> str:
        self.submissions.append(kwargs)
        return "task-1"


def binding(**overrides):
    data = {
        "enabled": True,
        "repository_id": 100,
        "installation_id": 10,
        "full_name": "octo/example",
        "default_branch": "main",
        "agent_name": "default",
        "trigger_label": "kaka-agent",
        "mention_triggers_json": '["@kaka-agent", "/kaka"]',
        "notify_platform": None,
        "notify_external_id": None,
        "notify_channel_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def issue_payload(**overrides):
    payload = {
        "action": "opened",
        "repository": {"id": 100, "full_name": "octo/example", "default_branch": "main"},
        "installation": {"id": 10},
        "sender": {"type": "User", "login": "octocat"},
        "issue": {
            "number": 7,
            "title": "Fix failing test",
            "body": "The test fails.",
            "html_url": "https://github.com/octo/example/issues/7",
            "labels": [{"name": "kaka-agent"}],
        },
    }
    payload.update(overrides)
    return payload


def review_comment_payload(**overrides):
    payload = {
        "action": "created",
        "repository": {"id": 100, "full_name": "octo/example", "default_branch": "main"},
        "installation": {"id": 10},
        "sender": {"type": "User", "login": "reviewer"},
        "pull_request": {
            "number": 2,
            "title": "Fix failing test",
            "html_url": "https://github.com/octo/example/pull/2",
            "head": {
                "ref": "kaka/default/issue-7-delivery",
                "sha": "abc123",
                "repo": {"full_name": "octo/example"},
            },
            "base": {"ref": "main"},
        },
        "comment": {
            "id": 123,
            "body": "Please handle the None case here.",
            "path": "agent/example.py",
            "line": 42,
            "diff_hunk": "@@ -39,7 +39,7 @@",
            "html_url": "https://github.com/octo/example/pull/2#discussion_r123",
        },
    }
    payload.update(overrides)
    return payload


def make_service(tmp_path: Path, store: FakeStore) -> GitHubAutomationService:
    service = GitHubAutomationService(
        client=FakeClient(),
        workspace_manager=FakeWorkspace(tmp_path),
    )
    service.store = store
    return service


def test_verify_webhook_signature_accepts_valid_signature() -> None:
    import hmac
    from hashlib import sha256

    body = b'{"ok": true}'
    secret = "secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()

    assert verify_webhook_signature(secret=secret, body=body, signature_header=signature)
    assert not verify_webhook_signature(secret=secret, body=body, signature_header="sha256=bad")


@pytest.mark.asyncio
async def test_webhook_ignores_duplicate_delivery(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeStore(binding(), first_seen=False))

    result = await service.handle_webhook(
        event="issues",
        delivery_id="delivery-1",
        payload=issue_payload(),
    )

    assert result == {"status": "ignored", "reason": "duplicate_delivery"}


@pytest.mark.asyncio
async def test_issue_label_trigger_submits_agent_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    service = make_service(tmp_path, FakeStore(binding()))

    result = await service.handle_webhook(
        event="issues",
        delivery_id="abcdef123456",
        payload=issue_payload(),
    )

    assert result == {"status": "submitted", "task_id": "task-1"}
    submission = task_manager.submissions[0]
    assert submission["agent_name"] == "default"
    assert submission["workspace"].locator == str(tmp_path)
    assert submission["workspace"].label == "octo/example"
    assert "Fix failing test" in submission["request"]


@pytest.mark.asyncio
async def test_comment_mention_trigger_submits_agent_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    service = make_service(tmp_path, FakeStore(binding()))
    payload = issue_payload(
        action="created",
        comment={"body": "@kaka-agent please handle this"},
    )

    result = await service.handle_webhook(
        event="issue_comment",
        delivery_id="comment-1",
        payload=payload,
    )

    assert result == {"status": "submitted", "task_id": "task-1"}


@pytest.mark.asyncio
async def test_review_comment_submits_agent_task_on_pr_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    store = FakeStore(binding())
    service = make_service(tmp_path, store)
    workspace = service.workspace_manager

    result = await service.handle_webhook(
        event="pull_request_review_comment",
        delivery_id="review-1",
        payload=review_comment_payload(),
    )

    assert result == {"status": "submitted", "task_id": "task-1"}
    assert workspace.prepared[0]["branch"] == "kaka/default/issue-7-delivery"
    assert workspace.prepared[0]["base_branch"] == "main"
    submission = task_manager.submissions[0]
    assert submission["workspace"].locator == str(tmp_path)
    assert submission["workspace"].label == "octo/example"
    assert "Review comment:" in submission["request"]
    assert "Please handle the None case here." in submission["request"]
    assert "agent/example.py" in submission["request"]


@pytest.mark.asyncio
async def test_review_comment_ignores_fork_pull_request(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeStore(binding()))
    payload = review_comment_payload(
        pull_request={
            "number": 2,
            "title": "Fix failing test",
            "html_url": "https://github.com/octo/example/pull/2",
            "head": {
                "ref": "contrib-fix",
                "sha": "abc123",
                "repo": {"full_name": "contrib/example"},
            },
            "base": {"ref": "main"},
        },
    )

    result = await service.handle_webhook(
        event="pull_request_review_comment",
        delivery_id="review-1",
        payload=payload,
    )

    assert result == {"status": "ignored", "reason": "fork_pull_request"}


@pytest.mark.asyncio
async def test_sync_installations_upserts_repositories(tmp_path: Path) -> None:
    store = FakeStore()
    service = make_service(tmp_path, store)
    service.settings = SimpleNamespace(
        is_configured=True,
        default_agent="default",
        trigger_label="kaka-agent",
        mention_triggers=("@kaka-agent", "/kaka"),
    )

    result = await service.sync_installations()

    assert result == {"installations": 1, "repositories": 1}
    assert store.installations[0]["id"] == 10
    assert store.repositories[0][0]["full_name"] == "octo/example"


@pytest.mark.asyncio
async def test_workspace_prepare_uses_reusable_repo_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_run_git(args, *, cwd, token=None, capture=False):
        calls.append({"args": args, "cwd": cwd, "token": token})
        return ""

    import agent.modules.github.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "_run_git", fake_run_git)
    manager = GitHubWorkspaceManager(root=tmp_path)

    prepared = await manager.prepare(
        full_name="octo/example",
        default_branch="main",
        branch="kaka/default/issue-1-delivery",
        token="secret-token",
    )

    assert prepared.path == tmp_path / "octo" / "example"
    assert calls[0]["args"][0] == "clone"
    assert calls[0]["token"] == "secret-token"
    assert calls[2]["args"] == ["checkout", "-B", "kaka/default/issue-1-delivery", "origin/main"]


@pytest.mark.asyncio
async def test_workspace_prepare_existing_branch_uses_remote_pr_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_run_git(args, *, cwd, token=None, capture=False):
        calls.append({"args": args, "cwd": cwd, "token": token})
        return ""

    import agent.modules.github.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "_run_git", fake_run_git)
    manager = GitHubWorkspaceManager(root=tmp_path)

    prepared = await manager.prepare_existing_branch(
        full_name="octo/example",
        branch="kaka/default/issue-7-delivery",
        base_branch="main",
        token="secret-token",
    )

    assert prepared.path == tmp_path / "octo" / "example"
    assert prepared.branch == "kaka/default/issue-7-delivery"
    assert calls[0]["args"][0] == "clone"
    assert calls[2]["args"] == [
        "checkout",
        "-B",
        "kaka/default/issue-7-delivery",
        "origin/kaka/default/issue-7-delivery",
    ]


@pytest.mark.asyncio
async def test_publish_task_result_opens_pr_when_diff_exists(tmp_path: Path) -> None:
    client = FakeClient()
    workspace = FakeWorkspace(tmp_path, has_changes=True)
    service = GitHubAutomationService(client=client, workspace_manager=workspace)
    context = SimpleNamespace(
        installation_id=10,
        repository_full_name="octo/example",
        issue_number=7,
        issue_title="Fix failing test",
        issue_url="https://github.com/octo/example/issues/7",
        branch="kaka/default/issue-7-delivery",
        base_branch="main",
        workspace_path=tmp_path,
    )
    task = BackgroundTask(request="work", agent_name="default", result="Done")

    await service.publish_task_result(task, context)

    assert workspace.commits
    assert workspace.pushes
    assert client.pull_requests[0]["head"] == "kaka/default/issue-7-delivery"
    assert "Pull request:" in task.result


@pytest.mark.asyncio
async def test_publish_task_result_updates_existing_pr_for_review_comment(tmp_path: Path) -> None:
    client = FakeClient()
    workspace = FakeWorkspace(tmp_path, has_changes=True)
    service = GitHubAutomationService(client=client, workspace_manager=workspace)
    context = SimpleNamespace(
        installation_id=10,
        repository_full_name="octo/example",
        issue_number=2,
        issue_title="Fix failing test",
        issue_url="https://github.com/octo/example/pull/2",
        branch="kaka/default/issue-7-delivery",
        base_branch="main",
        workspace_path=tmp_path,
        completion_mode="update_pull_request",
        review_comment_id=123,
    )
    task = BackgroundTask(request="work", agent_name="default", result="Done")

    await service.publish_task_result(task, context)

    assert workspace.commits[0]["message"] == "Address review feedback on PR #2"
    assert workspace.pushes[0]["branch"] == "kaka/default/issue-7-delivery"
    assert not client.pull_requests
    assert client.review_comment_replies[0]["comment_id"] == 123
    assert "Pull request updated:" in task.result


@pytest.mark.asyncio
async def test_publish_task_result_comments_when_no_diff(tmp_path: Path) -> None:
    client = FakeClient()
    workspace = FakeWorkspace(tmp_path, has_changes=False)
    service = GitHubAutomationService(client=client, workspace_manager=workspace)
    context = SimpleNamespace(
        installation_id=10,
        repository_full_name="octo/example",
        issue_number=7,
        issue_title="Fix failing test",
        issue_url="https://github.com/octo/example/issues/7",
        branch="kaka/default/issue-7-delivery",
        base_branch="main",
        workspace_path=tmp_path,
    )
    task = BackgroundTask(request="work", agent_name="default", result="Done")

    await service.publish_task_result(task, context)

    assert not workspace.commits
    assert client.comments
    assert "No repository changes" in task.result
