from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agent.modules.agent_runtime.background_tasks import BackgroundTask
from agent.modules.github.service import GitHubAutomationService, verify_webhook_signature
from agent.modules.github.workspace import PreparedWorkspace, GitHubWorkspaceManager, sanitize_branch_name


@pytest_asyncio.fixture()
async def github_store_db(tmp_path):
    from agent.shared.infrastructure.db import (
        Base,
        close_async_engine,
        initialize_async_engine,
    )
    from agent.shared.infrastructure.db.models import load_orm_models

    await close_async_engine()

    db_path = tmp_path / "github_store.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"

    from agent.bootstrap.container import create_test_container, set_active_container

    set_active_container(create_test_container(database_url=db_url))

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)

    try:
        yield
    finally:
        await close_async_engine()


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
    def __init__(self, binding=None, first_seen: bool = True, claim_ok=True) -> None:
        self.binding = binding
        self.first_seen = first_seen
        self.claim_ok = claim_ok
        self.claims = []
        self.released_claims = []
        self.installations = []
        self.repositories = []

    async def mark_delivery_seen(self, *args, **kwargs) -> bool:
        return self.first_seen

    async def try_claim_issue_task(self, *args, **kwargs) -> bool:
        self.claims.append((args, kwargs))
        if isinstance(self.claim_ok, list):
            return self.claim_ok.pop(0)
        return self.claim_ok

    async def release_issue_task_claim(self, *args, **kwargs) -> None:
        self.released_claims.append(args)

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
        self.shared_checkouts = []
        self.commits = []
        self.pushes = []
        self.discarded_worktrees = []
        self.preserved = []

    async def ensure_shared_checkout(self, **kwargs):
        self.shared_checkouts.append(kwargs)
        return self.tmp_path

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

    async def discard_worktree(self, **kwargs) -> None:
        self.discarded_worktrees.append(kwargs)

    async def prune_orphaned_worktrees(self) -> int:
        return 0

    async def preserve_worktree_changes(self, **kwargs) -> None:
        self.preserved.append(kwargs)


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
        "trigger_label": "k41-agent",
        "mention_triggers_json": '["@k41-agent", "/k41"]',
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
            "labels": [{"name": "k41-agent"}],
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
                "ref": "k41/default/issue-7-delivery",
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


def test_github_workspace_manager_uses_configured_github_workspace_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "github-workspaces"

    class FakeConfig:
        def get_path(self, key: str, default: str = "") -> Path:
            assert key == "workspace.github.root"
            assert default == "~/.k41-agent/github-workspaces"
            return configured_root

    monkeypatch.setattr(
        "agent.modules.github.config.get_config_service",
        lambda: FakeConfig(),
    )

    manager = GitHubWorkspaceManager()

    assert manager.root == configured_root


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
async def test_issue_opened_and_labeled_pair_submits_single_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    store = FakeStore(binding(), claim_ok=[True, False])
    service = make_service(tmp_path, store)

    opened = await service.handle_webhook(
        event="issues",
        delivery_id="delivery-opened",
        payload=issue_payload(action="opened"),
    )
    labeled = await service.handle_webhook(
        event="issues",
        delivery_id="delivery-labeled",
        payload=issue_payload(action="labeled", label={"name": "k41-agent"}),
    )

    assert opened == {"status": "submitted", "task_id": "task-1"}
    assert labeled == {"status": "ignored", "reason": "duplicate_issue_task"}
    assert len(task_manager.submissions) == 1
    assert len(store.claims) == 2
    assert store.claims[0][0] == ("octo/example", 7)
    assert store.claims[1][0] == ("octo/example", 7)


@pytest.mark.asyncio
async def test_issue_trigger_ignored_when_issue_task_already_claimed(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeStore(binding(), claim_ok=False))

    result = await service.handle_webhook(
        event="issues",
        delivery_id="delivery-1",
        payload=issue_payload(),
    )

    assert result == {"status": "ignored", "reason": "duplicate_issue_task"}


@pytest.mark.asyncio
async def test_comment_trigger_does_not_consume_issue_task_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    store = FakeStore(binding())
    service = make_service(tmp_path, store)
    payload = issue_payload(
        action="created",
        comment={"body": "@k41-agent please handle this"},
    )

    result = await service.handle_webhook(
        event="issue_comment",
        delivery_id="comment-1",
        payload=payload,
    )

    assert result == {"status": "submitted", "task_id": "task-1"}
    assert store.claims == []


@pytest.mark.asyncio
async def test_try_claim_issue_task_dedupes_within_ttl(github_store_db) -> None:
    from agent.modules.github.repository import GitHubRepositoryStore

    store = GitHubRepositoryStore()

    first = await store.try_claim_issue_task(
        "octo/example", 7, delivery_id="d1", action="opened", ttl_seconds=600
    )
    second = await store.try_claim_issue_task(
        "octo/example", 7, delivery_id="d2", action="labeled", ttl_seconds=600
    )
    other_issue = await store.try_claim_issue_task(
        "octo/example", 8, delivery_id="d3", action="opened", ttl_seconds=600
    )

    assert first is True
    assert second is False
    assert other_issue is True


@pytest.mark.asyncio
async def test_try_claim_issue_task_allows_takeover_after_expiry(github_store_db) -> None:
    from agent.modules.github.repository import GitHubRepositoryStore

    store = GitHubRepositoryStore()

    first = await store.try_claim_issue_task(
        "octo/example", 7, delivery_id="d1", action="opened", ttl_seconds=600
    )
    expired = await store.try_claim_issue_task(
        "octo/example", 7, delivery_id="d2", action="labeled", ttl_seconds=0
    )

    assert first is True
    assert expired is True


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
    assert submission["workspace"].metadata["source"] == "github"
    assert submission["workspace"].metadata["repository_full_name"] == "octo/example"
    assert submission["workspace"].metadata["branch"] == "k41/default/issue-7-abcdef12"
    assert "Fix failing test" in submission["request"]


@pytest.mark.asyncio
async def test_issue_task_cleanup_hook_discards_worktree(
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
    cleanup_hook = task_manager.submissions[0]["cleanup_hook"]
    assert cleanup_hook is not None

    await cleanup_hook(BackgroundTask(request="work", agent_name="default"))

    assert service.workspace_manager.discarded_worktrees == [
        {"full_name": "octo/example", "path": Path(str(tmp_path))}
    ]


@pytest.mark.asyncio
async def test_issue_label_scope_can_disable_repository_automation(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeStore(binding(issue_label_enabled=False)))

    result = await service.handle_webhook(
        event="issues",
        delivery_id="delivery-1",
        payload=issue_payload(),
    )

    assert result == {"status": "ignored", "reason": "issue_label_disabled"}


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
        comment={"body": "@k41-agent please handle this"},
    )

    result = await service.handle_webhook(
        event="issue_comment",
        delivery_id="comment-1",
        payload=payload,
    )

    assert result == {"status": "submitted", "task_id": "task-1"}


@pytest.mark.asyncio
async def test_comment_scope_can_disable_repository_automation(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeStore(binding(issue_comment_enabled=False)))
    payload = issue_payload(
        action="created",
        comment={"body": "@k41-agent please handle this"},
    )

    result = await service.handle_webhook(
        event="issue_comment",
        delivery_id="comment-1",
        payload=payload,
    )

    assert result == {"status": "ignored", "reason": "issue_comment_disabled"}


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
    assert workspace.prepared[0]["branch"] == "k41/default/issue-7-delivery"
    assert workspace.prepared[0]["base_branch"] == "main"
    submission = task_manager.submissions[0]
    assert submission["workspace"].locator == str(tmp_path)
    assert submission["workspace"].label == "octo/example"
    assert submission["workspace"].metadata["repository_full_name"] == "octo/example"
    assert submission["workspace"].metadata["branch"] == "k41/default/issue-7-delivery"
    assert "Review comment:" in submission["request"]
    assert "Please handle the None case here." in submission["request"]
    assert "agent/example.py" in submission["request"]


@pytest.mark.asyncio
async def test_review_comment_scope_can_disable_repository_automation(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeStore(binding(pr_review_comment_enabled=False)))

    result = await service.handle_webhook(
        event="pull_request_review_comment",
        delivery_id="review-1",
        payload=review_comment_payload(),
    )

    assert result == {"status": "ignored", "reason": "pr_review_comment_disabled"}


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
async def test_repository_optimization_settings_flow_to_background_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    optimized_binding = binding(
        repository_instructions="Always run uv tests.",
        provider_name="main-provider",
        model_name="fast-model",
        context_trim_threshold=24000,
        tool_policy_mode="custom",
        allowed_tools_json='["read_file", "write_file"]',
        allowed_skills_json='["repo-skill"]',
        branch_prefix="repo-bot",
    )
    service = make_service(tmp_path, FakeStore(optimized_binding))

    result = await service.handle_webhook(
        event="issues",
        delivery_id="abcdef123456",
        payload=issue_payload(),
    )

    assert result == {"status": "submitted", "task_id": "task-1"}
    submission = task_manager.submissions[0]
    assert "Always run uv tests." in submission["request"]
    assert submission["provider"] == "main-provider"
    assert submission["model"] == "fast-model"
    assert submission["context_trim_threshold"] == 24000
    assert submission["allowed_tool_names"] == ["read_file", "write_file"]
    assert submission["allowed_skill_names"] == ["repo-skill"]
    assert submission["workspace"].metadata["branch"] == "repo-bot/default/issue-7-abcdef12"


@pytest.mark.asyncio
async def test_sync_installations_upserts_repositories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore()
    service = make_service(tmp_path, store)
    monkeypatch.setattr(
        "agent.modules.github.service.get_github_settings",
        lambda: SimpleNamespace(
            is_configured=True,
            default_agent="default",
            trigger_label="k41-agent",
            mention_triggers=("@k41-agent", "/k41"),
        ),
    )

    result = await service.sync_installations()

    assert result == {"installations": 1, "repositories": 1}
    assert store.installations[0]["id"] == 10
    assert store.repositories[0][0]["full_name"] == "octo/example"


@pytest.mark.asyncio
async def test_submit_repository_task_uses_repository_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    optimized_binding = binding(
        repository_instructions="Prefer small focused changes.",
        provider_name="repo-provider",
        model_name="repo-model",
        context_trim_threshold=12000,
        tool_policy_mode="custom",
        allowed_tools_json='["read_file"]',
        allowed_skills_json='["repo-skill"]',
        notify_platform="telegram",
        notify_external_id="123",
        notify_channel_id="123",
    )
    service = make_service(tmp_path, FakeStore(optimized_binding))

    task_id = await service.submit_repository_task(
        100,
        request="Fix the failing build",
    )

    assert task_id == "task-1"
    submission = task_manager.submissions[0]
    assert "Fix the failing build" in submission["request"]
    assert "Prefer small focused changes." in submission["request"]
    assert submission["agent_name"] == "default"
    assert submission["provider"] == "repo-provider"
    assert submission["model"] == "repo-model"
    assert submission["context_trim_threshold"] == 12000
    assert submission["allowed_tool_names"] == ["read_file"]
    assert submission["allowed_skill_names"] == ["repo-skill"]
    assert submission["notify_channel"].platform == "telegram"
    assert submission["workspace"].metadata["repository_full_name"] == "octo/example"


def test_github_migration_adds_repository_binding_columns(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, inspect, text

    from agent.modules.github.migrations import migrate_github_tables

    db_path = tmp_path / "github.sqlite"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE github_repository_bindings ("
                    "id INTEGER PRIMARY KEY, repository_id INTEGER NOT NULL)"
                )
            )
    finally:
        engine.dispose()

    migrate_github_tables(database_url)
    migrate_github_tables(database_url)

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("github_repository_bindings")}
    finally:
        engine.dispose()

    assert "repository_instructions" in columns
    assert "provider_name" in columns
    assert "model_name" in columns
    assert "allowed_tools_json" in columns
    assert "allowed_skills_json" in columns
    assert "branch_prefix" in columns


@pytest.mark.asyncio
async def test_workspace_prepare_uses_isolated_worktree_per_task(
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
        branch="k41/default/issue-1-delivery",
        token="secret-token",
    )

    worktrees_root = tmp_path / "octo" / "example.worktrees"
    assert prepared.path.parent == worktrees_root
    assert prepared.path.name.startswith("k41-default-issue-1-delivery-")
    assert prepared.branch == "k41/default/issue-1-delivery"
    assert prepared.base_branch == "main"
    assert calls[0]["args"][0] == "clone"
    assert calls[0]["token"] == "secret-token"
    assert calls[1]["args"] == ["fetch", "--prune", "origin"]
    worktree_add = calls[2]
    assert worktree_add["args"][:3] == ["worktree", "add", "--detach"]
    assert worktree_add["args"][3] == str(prepared.path)
    assert worktree_add["args"][4] == "origin/main"
    assert worktree_add["cwd"] == tmp_path / "octo" / "example"


@pytest.mark.asyncio
async def test_workspace_prepare_creates_distinct_worktrees_for_concurrent_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_run_git(args, *, cwd, token=None, capture=False):
        return ""

    import agent.modules.github.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "_run_git", fake_run_git)
    manager = GitHubWorkspaceManager(root=tmp_path)

    first = await manager.prepare(
        full_name="octo/example",
        default_branch="main",
        branch="k41/default/issue-1-aaaaaaaa",
        token="secret-token",
    )
    second = await manager.prepare(
        full_name="octo/example",
        default_branch="main",
        branch="k41/default/issue-1-bbbbbbbb",
        token="secret-token",
    )

    assert first.path != second.path
    assert first.path.parent == second.path.parent


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
        branch="k41/default/issue-7-delivery",
        base_branch="main",
        token="secret-token",
    )

    assert prepared.path.parent == tmp_path / "octo" / "example.worktrees"
    assert prepared.path.name.startswith("k41-default-issue-7-delivery-")
    assert prepared.branch == "k41/default/issue-7-delivery"
    assert calls[0]["args"][0] == "clone"
    worktree_add = calls[2]
    assert worktree_add["args"][:3] == ["worktree", "add", "--detach"]
    assert worktree_add["args"][4] == "origin/k41/default/issue-7-delivery"


@pytest.mark.asyncio
async def test_discard_worktree_removes_task_worktree(
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
    repo_path = tmp_path / "octo" / "example"
    (repo_path / ".git").mkdir(parents=True)
    worktree_path = tmp_path / "octo" / "example.worktrees" / "k41-task-12345678"

    await manager.discard_worktree(full_name="octo/example", path=worktree_path)

    assert calls == [
        {
            "args": ["worktree", "remove", "--force", str(worktree_path)],
            "cwd": repo_path,
            "token": None,
        }
    ]


@pytest.mark.asyncio
async def test_discard_worktree_skips_missing_base_clone(
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

    await manager.discard_worktree(
        full_name="octo/example",
        path=tmp_path / "octo" / "example.worktrees" / "k41-task-12345678",
    )

    assert calls == []


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
        branch="k41/default/issue-7-delivery",
        base_branch="main",
        workspace_path=tmp_path,
    )
    task = BackgroundTask(request="work", agent_name="default", result="Done")

    await service.publish_task_result(task, context)

    assert workspace.commits
    assert workspace.pushes
    assert client.pull_requests[0]["head"] == "k41/default/issue-7-delivery"
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
        branch="k41/default/issue-7-delivery",
        base_branch="main",
        workspace_path=tmp_path,
        completion_mode="update_pull_request",
        review_comment_id=123,
    )
    task = BackgroundTask(request="work", agent_name="default", result="Done")

    await service.publish_task_result(task, context)

    assert workspace.commits[0]["message"] == "Address review feedback on PR #2"
    assert workspace.pushes[0]["branch"] == "k41/default/issue-7-delivery"
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
        branch="k41/default/issue-7-delivery",
        base_branch="main",
        workspace_path=tmp_path,
    )
    task = BackgroundTask(request="work", agent_name="default", result="Done")

    await service.publish_task_result(task, context)

    assert not workspace.commits
    assert client.comments
    assert "No repository changes" in task.result


@pytest.mark.asyncio
async def test_issue_claim_released_when_task_submission_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    store = FakeStore(binding())
    service = make_service(tmp_path, store)

    async def failing_prepare(**kwargs):
        raise RuntimeError("clone failed")

    service.workspace_manager.prepare = failing_prepare

    with pytest.raises(RuntimeError):
        await service.handle_webhook(
            event="issues",
            delivery_id="delivery-1",
            payload=issue_payload(),
        )

    assert store.released_claims == [("octo/example", 7)]
    assert task_manager.submissions == []


@pytest.mark.asyncio
async def test_comment_trigger_failure_does_not_release_issue_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_manager = FakeTaskManager()
    import agent.modules.github.service as service_module

    monkeypatch.setattr(service_module, "get_background_task_manager", lambda: task_manager)
    store = FakeStore(binding())
    service = make_service(tmp_path, store)

    async def failing_prepare(**kwargs):
        raise RuntimeError("clone failed")

    service.workspace_manager.prepare = failing_prepare
    payload = issue_payload(
        action="created",
        comment={"body": "@k41-agent please handle this"},
    )

    with pytest.raises(RuntimeError):
        await service.handle_webhook(
            event="issue_comment",
            delivery_id="comment-1",
            payload=payload,
        )

    assert store.released_claims == []


@pytest.mark.asyncio
async def test_publish_task_result_preserves_changes_when_push_fails(
    tmp_path: Path,
) -> None:
    client = FakeClient()

    class PushFailsWorkspace(FakeWorkspace):
        async def push_branch(self, **kwargs):
            raise RuntimeError("push rejected")

    workspace = PushFailsWorkspace(tmp_path, has_changes=True)
    service = GitHubAutomationService(client=client, workspace_manager=workspace)
    context = SimpleNamespace(
        installation_id=10,
        repository_full_name="octo/example",
        issue_number=7,
        issue_title="Fix failing test",
        issue_url="https://github.com/octo/example/issues/7",
        branch="k41/default/issue-7-delivery",
        base_branch="main",
        workspace_path=tmp_path,
    )
    task = BackgroundTask(request="work", agent_name="default", result="Done")

    await service.publish_task_result(task, context)

    assert workspace.preserved == [
        {
            "full_name": "octo/example",
            "path": tmp_path,
            "backup_branch": f"backup/kai-{task.task_id}",
        }
    ]
    assert client.comments
    assert "Failed to commit/push changes" in task.result
    assert f"backup/kai-{task.task_id}" in task.result


@pytest.mark.asyncio
async def test_prune_orphaned_worktrees_removes_leftover_worktrees(
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

    repo_path = tmp_path / "octo" / "example"
    (repo_path / ".git").mkdir(parents=True)
    orphan = tmp_path / "octo" / "example.worktrees" / "k41-task-12345678"
    orphan.mkdir(parents=True)
    (orphan / ".git").write_text(
        f"gitdir: {repo_path / '.git' / 'worktrees' / orphan.name}\n",
        encoding="utf-8",
    )

    pruned = await manager.prune_orphaned_worktrees()

    assert pruned == 1
    assert calls == [
        {
            "args": ["worktree", "remove", "--force", str(orphan)],
            "cwd": repo_path,
            "token": None,
        }
    ]


@pytest.mark.asyncio
async def test_prune_orphaned_worktrees_removes_orphan_without_base_clone(
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

    orphan = tmp_path / "octo" / "example.worktrees" / "k41-task-12345678"
    orphan.mkdir(parents=True)
    (orphan / ".git").write_text(
        "gitdir: "
        + (tmp_path / "octo" / "example" / ".git" / "worktrees" / orphan.name).as_posix()
        + "\n",
        encoding="utf-8",
    )
    orphan_file = orphan / "worktree-file.txt"
    orphan_file.write_text("leftover", encoding="utf-8")

    pruned = await manager.prune_orphaned_worktrees()

    assert pruned == 1
    assert calls == []
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_prune_orphaned_worktrees_skips_non_worktree_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A repository whose name ends with .worktrees must not lose its files."""
    calls = []

    async def fake_run_git(args, *, cwd, token=None, capture=False):
        calls.append({"args": args, "cwd": cwd, "token": token})
        return ""

    import agent.modules.github.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "_run_git", fake_run_git)
    manager = GitHubWorkspaceManager(root=tmp_path)

    # A legitimate repository named "example.worktrees" cloned under octo/.
    repo_path = tmp_path / "octo" / "example.worktrees"
    (repo_path / ".git").mkdir(parents=True)
    src_dir = repo_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hi')", encoding="utf-8")

    pruned = await manager.prune_orphaned_worktrees()

    assert pruned == 0
    assert calls == []
    assert (src_dir / "main.py").exists()


@pytest.mark.asyncio
async def test_prune_orphaned_worktrees_falls_back_when_git_remove_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_run_git(args, *, cwd, token=None, capture=False):
        calls.append({"args": args, "cwd": cwd, "token": token})
        if args[:2] == ["worktree", "remove"]:
            raise RuntimeError("worktree is locked")
        return ""

    import agent.modules.github.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "_run_git", fake_run_git)
    manager = GitHubWorkspaceManager(root=tmp_path)

    repo_path = tmp_path / "octo" / "example"
    (repo_path / ".git").mkdir(parents=True)
    orphan = tmp_path / "octo" / "example.worktrees" / "k41-task-12345678"
    orphan.mkdir(parents=True)
    (orphan / ".git").write_text(
        f"gitdir: {repo_path / '.git' / 'worktrees' / orphan.name}\n",
        encoding="utf-8",
    )
    (orphan / "leftover.txt").write_text("work", encoding="utf-8")

    pruned = await manager.prune_orphaned_worktrees()

    assert pruned == 1
    assert not orphan.exists()
    assert calls == [
        {
            "args": ["worktree", "remove", "--force", str(orphan)],
            "cwd": repo_path,
            "token": None,
        },
        {
            "args": ["worktree", "prune"],
            "cwd": repo_path,
            "token": None,
        },
    ]


@pytest.mark.asyncio
async def test_worktree_discarded_when_task_submission_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import agent.modules.github.service as service_module

    class FailingTaskManager:
        async def submit(self, **kwargs) -> str:
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        service_module,
        "get_background_task_manager",
        lambda: FailingTaskManager(),
    )
    store = FakeStore(binding())
    service = make_service(tmp_path, store)

    with pytest.raises(RuntimeError, match="db unavailable"):
        await service.handle_webhook(
            event="issues",
            delivery_id="delivery-1",
            payload=issue_payload(),
        )

    assert service.workspace_manager.discarded_worktrees == [
        {"full_name": "octo/example", "path": Path(str(tmp_path))}
    ]
    assert store.released_claims == [("octo/example", 7)]


@pytest.mark.asyncio
async def test_worktree_discarded_when_review_task_submission_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import agent.modules.github.service as service_module

    class FailingTaskManager:
        async def submit(self, **kwargs) -> str:
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        service_module,
        "get_background_task_manager",
        lambda: FailingTaskManager(),
    )
    service = make_service(tmp_path, FakeStore(binding()))

    with pytest.raises(RuntimeError, match="db unavailable"):
        await service.handle_webhook(
            event="pull_request_review_comment",
            delivery_id="delivery-2",
            payload=review_comment_payload(),
        )

    assert service.workspace_manager.discarded_worktrees == [
        {"full_name": "octo/example", "path": Path(str(tmp_path))}
    ]


@pytest.mark.asyncio
async def test_issue_claim_ttl_outlives_max_task_duration() -> None:
    import agent.modules.github.service as service_module

    assert service_module.ISSUE_TASK_CLAIM_TTL_SECONDS >= service_module.DEFAULT_TASK_TIMEOUT
