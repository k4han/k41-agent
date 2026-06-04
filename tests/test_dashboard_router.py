from datetime import datetime, timedelta
import importlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.delivery.http.dashboard.auth_router import router as auth_router
from agent.delivery.http.dashboard.router import router as dashboard_router
from agent.delivery.http.dashboard.spa import STATIC_DIR
from agent.modules.admin_auth import get_current_admin
from agent.modules.channels import ChannelManager

_DASHBOARD_ROUTE_MODULES = (
    "agent.delivery.http.dashboard.routes.shared",
    "agent.delivery.http.dashboard.routes.conversations",
    "agent.delivery.http.dashboard.routes.scheduler",
    "agent.delivery.http.dashboard.routes.workspace",
    "agent.delivery.http.dashboard.routes.tasks",
    "agent.delivery.http.dashboard.routes.github",
    "agent.delivery.http.dashboard.routes.dashboard",
    "agent.delivery.http.dashboard.routes.agents",
    "agent.delivery.http.dashboard.routes.channels",
    "agent.delivery.http.dashboard.routes.providers",
    "agent.delivery.http.dashboard.routes.skills",
    "agent.delivery.http.dashboard.routes.settings",
    "agent.delivery.http.dashboard.routes.spa",
)


def _patch_dashboard_attr(monkeypatch: pytest.MonkeyPatch, name: str, value) -> None:
    patched = False
    for mod_name in _DASHBOARD_ROUTE_MODULES:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, value)
            patched = True
    if not patched:
        raise AttributeError(f"No dashboard route module exposes {name!r}")


def _dashboard_attr(name: str):
    for mod_name in _DASHBOARD_ROUTE_MODULES:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"No dashboard route module exposes {name!r}")


async def idle_runner() -> None:
    return None


class DateTrigger:
    pass


class FakeJob:
    def __init__(self, trigger: object, kwargs: dict, next_run_time: datetime | None):
        self.id = "job-1"
        self.trigger = trigger
        self.kwargs = kwargs
        self.next_run_time = next_run_time


class FakeScheduler:
    timezone = ZoneInfo("Asia/Bangkok")

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.jobs: dict[str, FakeJob] = {}

    def add_job(self, func, trigger: str, kwargs: dict, **trigger_args) -> FakeJob:
        self.calls.append(
            {
                "func": func,
                "trigger": trigger,
                "kwargs": kwargs,
                "trigger_args": trigger_args,
            }
        )
        job = FakeJob(DateTrigger(), kwargs, trigger_args.get("run_date"))
        self.jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> FakeJob | None:
        return self.jobs.get(job_id)


def _create_dashboard_client(channel_manager: ChannelManager) -> TestClient:
    from agent.shared.config import ConfigService
    from agent.shared.config.default_source import DefaultConfigSource

    app = FastAPI()
    app.state.channel_manager = channel_manager
    app.state.config_service = ConfigService(sources=[DefaultConfigSource()])
    app.include_router(dashboard_router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app)


def _require_git() -> str:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required for workspace diff tests")
    return git


def _run_git(repo: Path, *args: str) -> None:
    git = _require_git()
    result = subprocess.run(
        [git, *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def _workspace_payload(path: str | Path) -> dict:
    return {
        "backend": "local",
        "locator": str(path),
        "label": str(path),
        "metadata": {},
    }


def _absolute(path: str | Path) -> str:
    return str(Path(path).resolve())


def _conversation_thread(thread_id: str, title: str, *, kind: str = "user") -> dict:
    return {
        "thread_id": thread_id,
        "platform": "api",
        "user_id": "dashboard",
        "channel_id": "123",
        "agent_name": "default",
        "title": title,
        "kind": kind,
        "created_at": None,
        "updated_at": None,
    }


def test_services_endpoint_returns_runtime_service_snapshot() -> None:
    channel_manager = ChannelManager()
    channel_manager.register("telegram", idle_runner)
    channel_manager.register("discord", idle_runner)

    client = _create_dashboard_client(channel_manager)
    response = client.get("/services")

    assert response.status_code == 200
    assert response.json() == {
        "services": [
            {"name": "telegram", "status": "stopped", "error": None},
            {"name": "discord", "status": "stopped", "error": None},
        ]
    }


def test_legacy_bots_routes_are_removed() -> None:
    client = _create_dashboard_client(ChannelManager())
    response = client.get("/bots")

    assert response.status_code == 404


def test_dashboard_spa_route_serves_index() -> None:
    client = _create_dashboard_client(ChannelManager())

    response = client.get("/scheduler")

    assert response.status_code == 200
    assert '<div id="root">' in response.text
    assert "/dashboard-assets/" in response.text

    chat_response = client.get("/c/api_dashboard_123")

    assert chat_response.status_code == 200
    assert '<div id="root">' in chat_response.text
    assert "/dashboard-assets/" in chat_response.text

    connections_response = client.get("/settings/connections")

    assert connections_response.status_code == 200
    assert '<div id="root">' in connections_response.text
    assert "/dashboard-assets/" in connections_response.text

    skills_response = client.get("/settings/skills")

    assert skills_response.status_code == 200
    assert '<div id="root">' in skills_response.text
    assert "/dashboard-assets/" in skills_response.text


def test_dashboard_api_overview_returns_runtime_snapshot() -> None:
    channel_manager = ChannelManager()
    channel_manager.register("telegram", idle_runner)

    client = _create_dashboard_client(channel_manager)
    response = client.get("/dashboard-api/overview")

    assert response.status_code == 200
    assert response.json() == {
        "services": [{"name": "telegram", "status": "stopped", "error": None}]
    }


def test_dashboard_api_skills_crud_and_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import agent.modules.skills as skills_module
    from agent.modules.skills.repository import FilesystemSkillRepository

    repo = FilesystemSkillRepository(skills_root=tmp_path)
    monkeypatch.setattr(skills_module, "_repository", repo)
    client = _create_dashboard_client(ChannelManager())

    empty = client.get("/dashboard-api/skills")
    assert empty.status_code == 200
    assert empty.json()["skills"] == []
    assert empty.json()["settings"]["skills.repository_dir"]["value"] == ".agent/skills"

    content = (
        "---\n"
        "name: api-skill\n"
        "description: API skill.\n"
        "---\n"
        "# Instructions\n"
        "Initial body.\n"
    )
    created = client.post(
        "/dashboard-api/skills",
        json={"name": "api-skill", "content": content},
    )
    assert created.status_code == 200
    assert created.json()["skill"]["name"] == "api-skill"

    detail = client.get("/dashboard-api/skills/api-skill")
    assert detail.status_code == 200
    assert "Initial body." in detail.json()["skill"]["content"]

    updated = client.put(
        "/dashboard-api/skills/api-skill",
        json={"name": "api-skill", "content": content.replace("Initial", "Updated")},
    )
    assert updated.status_code == 200
    assert "Updated body." in (tmp_path / "api-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    deleted = client.delete("/dashboard-api/skills/api-skill")
    assert deleted.status_code == 200
    assert not (tmp_path / "api-skill" / "SKILL.md").exists()

    (tmp_path / "reload-skill").mkdir()
    (tmp_path / "reload-skill" / "SKILL.md").write_text(
        "---\n"
        "name: reload-skill\n"
        "description: Reload skill.\n"
        "---\n"
        "# Instructions\n"
        "Reloaded.\n",
        encoding="utf-8",
    )
    reloaded = client.post("/dashboard-api/skills/reload")
    assert reloaded.status_code == 200
    assert [skill["name"] for skill in reloaded.json()["skills"]] == ["reload-skill"]


def test_dashboard_workspace_default_returns_absolute_path() -> None:
    client = _create_dashboard_client(ChannelManager())

    response = client.get("/dashboard-api/workspace/default")

    assert response.status_code == 200
    assert Path(response.json()["workspace"]["locator"]).is_absolute()


def test_dashboard_workspace_resolve_accepts_existing_local_path(tmp_path: Path) -> None:
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "local", "workspace": _workspace_payload(tmp_path)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "kind": "local",
        "label": str(tmp_path.resolve()),
        "workspace": {
            "backend": "local",
            "locator": str(tmp_path.resolve()),
            "label": str(tmp_path.resolve()),
            "metadata": {},
        },
    }


def test_dashboard_workspace_resolve_rejects_missing_local_path(tmp_path: Path) -> None:
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "local", "workspace": _workspace_payload(tmp_path / "missing")},
    )

    assert response.status_code == 404
    assert "Workspace does not exist" in response.json()["detail"]


def test_dashboard_workspace_resolve_creates_daytona_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    workspace = WorkspaceRef(
        backend="daytona",
        locator="sandbox-new",
        label="daytona:sandbox-new",
        metadata={"root": "workspace"},
    )

    _patch_dashboard_attr(monkeypatch, "create_daytona_workspace", lambda: workspace)
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "daytona"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "kind": "daytona",
        "label": "daytona:sandbox-new",
        "workspace": workspace.model_dump(),
    }


def test_dashboard_workspace_resolve_attaches_daytona_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    calls: dict[str, object] = {}

    def fake_attach(sandbox_id: str, *, root: str | None = None, label: str | None = None):
        calls["sandbox_id"] = sandbox_id
        calls["root"] = root
        calls["label"] = label
        return WorkspaceRef(
            backend="daytona",
            locator=sandbox_id,
            label=f"daytona:{sandbox_id}",
            metadata={"root": root or "workspace"},
        )

    _patch_dashboard_attr(monkeypatch, "attach_daytona_workspace", fake_attach)
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={
            "kind": "daytona",
            "workspace": {
                "backend": "daytona",
                "locator": "sandbox-existing",
                "label": "",
                "metadata": {"root": "custom-root"},
            },
        },
    )

    assert response.status_code == 200
    assert calls == {"sandbox_id": "sandbox-existing", "root": "custom-root", "label": None}
    assert response.json()["workspace"]["locator"] == "sandbox-existing"
    assert response.json()["workspace"]["metadata"]["root"] == "custom-root"


def test_dashboard_workspace_resolve_daytona_reports_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_create():
        raise ValueError("Daytona API key is not configured.")

    _patch_dashboard_attr(monkeypatch, "create_daytona_workspace", fake_create)
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "daytona"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Daytona API key is not configured."


def test_dashboard_workspace_resolve_creates_modal_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-new",
        label="modal:sb-new",
        metadata={"root": "/workspace"},
    )

    _patch_dashboard_attr(monkeypatch, "create_modal_workspace", lambda: workspace)
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "modal"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "kind": "modal",
        "label": "modal:sb-new",
        "workspace": workspace.model_dump(),
    }


def test_dashboard_workspace_resolve_attaches_modal_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    calls: dict[str, object] = {}

    def fake_attach(sandbox_id: str, *, root: str | None = None, label: str | None = None):
        calls["sandbox_id"] = sandbox_id
        calls["root"] = root
        calls["label"] = label
        return WorkspaceRef(
            backend="modal",
            locator=sandbox_id,
            label=f"modal:{sandbox_id}",
            metadata={"root": root or "/workspace"},
        )

    _patch_dashboard_attr(monkeypatch, "attach_modal_workspace", fake_attach)
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={
            "kind": "modal",
            "workspace": {
                "backend": "modal",
                "locator": "sb-existing",
                "label": "",
                "metadata": {"root": "/repo"},
            },
        },
    )

    assert response.status_code == 200
    assert calls == {"sandbox_id": "sb-existing", "root": "/repo", "label": None}
    assert response.json()["workspace"]["locator"] == "sb-existing"
    assert response.json()["workspace"]["metadata"]["root"] == "/repo"


def test_dashboard_workspace_resolve_modal_reports_disabled_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_create():
        raise ValueError("Modal workspace backend is disabled.")

    _patch_dashboard_attr(monkeypatch, "create_modal_workspace", fake_create)
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "modal"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Modal workspace backend is disabled."


def test_dashboard_workspace_browse_lists_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("ignored\n", encoding="utf-8")
    client = _create_dashboard_client(ChannelManager())

    response = client.get(
        "/dashboard-api/workspace/browse",
        params={"path": str(tmp_path)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == str(tmp_path.resolve())
    assert payload["parent"] == str(tmp_path.parent.resolve())
    assert [entry["name"] for entry in payload["entries"]] == ["docs", "src"]
    assert payload["roots"]


def test_dashboard_workspace_browse_rejects_missing_path(tmp_path: Path) -> None:
    client = _create_dashboard_client(ChannelManager())

    response = client.get(
        "/dashboard-api/workspace/browse",
        params={"path": str(tmp_path / "missing")},
    )

    assert response.status_code == 404
    assert "Directory does not exist" in response.json()["detail"]


def test_dashboard_workspace_resolve_uses_github_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:

    class FakeGitHubService:
        async def resolve_repository_workspace(self, repository_id: int):
            assert repository_id == 123
            return {
                "kind": "github",
                "label": "owner/repo",
                "workspace": _workspace_payload(tmp_path),
            }

    _patch_dashboard_attr(monkeypatch, "get_github_automation_service", lambda: FakeGitHubService(),
    )
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "github", "repository_id": 123},
    )

    assert response.status_code == 200
    assert response.json()["workspace"]["locator"] == str(tmp_path)


def test_dashboard_workspace_resolve_github_daytona_clones_inside_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    sandbox = WorkspaceRef(
        backend="daytona",
        locator="sb-day-1",
        label="daytona:sb-day-1",
        metadata={"root": "workspace"},
    )
    attached = WorkspaceRef(
        backend="daytona",
        locator="sb-day-1",
        label="acme/widgets",
        metadata={
            "root": "workspace",
            "source": "github",
            "repository_id": 88,
            "repository_full_name": "acme/widgets",
            "default_branch": "main",
            "repository_path": "acme/widgets",
        },
    )

    _patch_dashboard_attr(
        monkeypatch, "create_daytona_workspace", lambda: sandbox
    )

    async def fake_attach(workspace, *, repository_id, install_token=None):
        assert repository_id == 88
        assert workspace.locator == "sb-day-1"
        return attached

    _patch_dashboard_attr(
        monkeypatch, "attach_github_repository_to_workspace", fake_attach
    )
    remember_calls: list[tuple[str, WorkspaceRef]] = []

    async def fake_remember(thread_id, workspace):
        remember_calls.append((thread_id, workspace))
        return workspace

    _patch_dashboard_attr(
        monkeypatch, "remember_thread_workspace_ref", fake_remember
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={
            "kind": "github",
            "backend": "daytona",
            "repository_id": 88,
            "thread_id": "thread-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "daytona"
    assert payload["label"] == "acme/widgets"
    assert payload["is_github_source"] is True
    assert payload["workspace"]["metadata"]["source"] == "github"
    assert payload["workspace"]["metadata"]["repository_full_name"] == "acme/widgets"
    assert remember_calls == [("thread-1", attached)]


def test_dashboard_workspace_resolve_github_modal_clones_inside_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    sandbox = WorkspaceRef(
        backend="modal",
        locator="sb-mod-1",
        label="modal:sb-mod-1",
        metadata={"root": "/workspace"},
    )
    attached = WorkspaceRef(
        backend="modal",
        locator="sb-mod-1",
        label="acme/widgets",
        metadata={
            "root": "/workspace",
            "source": "github",
            "repository_id": 89,
            "repository_full_name": "acme/widgets",
            "default_branch": "main",
            "repository_path": "acme/widgets",
        },
    )

    _patch_dashboard_attr(
        monkeypatch, "create_modal_workspace", lambda: sandbox
    )

    async def fake_attach(workspace, *, repository_id, install_token=None):
        assert repository_id == 89
        return attached

    _patch_dashboard_attr(
        monkeypatch, "attach_github_repository_to_workspace", fake_attach
    )

    async def fake_remember(thread_id, workspace):
        return workspace

    _patch_dashboard_attr(
        monkeypatch, "remember_thread_workspace_ref", fake_remember
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={
            "kind": "github",
            "backend": "modal",
            "repository_id": 89,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "modal"
    assert payload["label"] == "acme/widgets"
    assert payload["is_github_source"] is True
    assert payload["workspace"]["locator"] == "sb-mod-1"


def test_dashboard_workspace_resolve_github_attaches_existing_daytona_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    calls: dict[str, object] = {}

    def fake_attach(sandbox_id, *, root=None, label=None):
        calls["sandbox_id"] = sandbox_id
        calls["root"] = root
        return WorkspaceRef(
            backend="daytona",
            locator=sandbox_id,
            label=f"daytona:{sandbox_id}",
            metadata={"root": root or "workspace"},
        )

    _patch_dashboard_attr(
        monkeypatch, "attach_daytona_workspace", fake_attach
    )

    async def fake_attach_github(workspace, *, repository_id, install_token=None):
        return WorkspaceRef(
            backend="daytona",
            locator=workspace.locator,
            label="acme/widgets",
            metadata={
                "root": "workspace",
                "source": "github",
                "repository_id": repository_id,
                "repository_full_name": "acme/widgets",
                "default_branch": "main",
                "repository_path": "acme/widgets",
            },
        )

    _patch_dashboard_attr(
        monkeypatch, "attach_github_repository_to_workspace", fake_attach_github
    )

    async def fake_remember(thread_id, workspace):
        return workspace

    _patch_dashboard_attr(
        monkeypatch, "remember_thread_workspace_ref", fake_remember
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={
            "kind": "github",
            "backend": "daytona",
            "repository_id": 90,
            "workspace": {
                "backend": "daytona",
                "locator": "sb-existing",
                "label": "",
                "metadata": {"root": "custom-root"},
            },
        },
    )

    assert response.status_code == 200
    assert calls == {"sandbox_id": "sb-existing", "root": "custom-root"}
    assert response.json()["workspace"]["locator"] == "sb-existing"
    assert response.json()["is_github_source"] is True


def test_dashboard_workspace_resolve_daytona_with_repository_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    sandbox = WorkspaceRef(
        backend="daytona",
        locator="sb-day-2",
        label="daytona:sb-day-2",
        metadata={"root": "workspace"},
    )
    attached = WorkspaceRef(
        backend="daytona",
        locator="sb-day-2",
        label="acme/widgets",
        metadata={
            "root": "workspace",
            "source": "github",
            "repository_id": 91,
            "repository_full_name": "acme/widgets",
            "default_branch": "main",
            "repository_path": "acme/widgets",
        },
    )

    _patch_dashboard_attr(
        monkeypatch, "create_daytona_workspace", lambda: sandbox
    )

    async def fake_attach(workspace, *, repository_id, install_token=None):
        assert repository_id == 91
        return attached

    _patch_dashboard_attr(
        monkeypatch, "attach_github_repository_to_workspace", fake_attach
    )

    async def fake_remember(thread_id, workspace):
        return workspace

    _patch_dashboard_attr(
        monkeypatch, "remember_thread_workspace_ref", fake_remember
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "daytona", "repository_id": 91},
    )

    assert response.status_code == 200
    assert response.json()["workspace"]["metadata"]["source"] == "github"


def test_dashboard_workspace_resolve_modal_with_repository_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceRef

    sandbox = WorkspaceRef(
        backend="modal",
        locator="sb-mod-2",
        label="modal:sb-mod-2",
        metadata={"root": "/workspace"},
    )
    attached = WorkspaceRef(
        backend="modal",
        locator="sb-mod-2",
        label="acme/widgets",
        metadata={
            "root": "/workspace",
            "source": "github",
            "repository_id": 92,
            "repository_full_name": "acme/widgets",
            "default_branch": "main",
            "repository_path": "acme/widgets",
        },
    )

    _patch_dashboard_attr(
        monkeypatch, "create_modal_workspace", lambda: sandbox
    )

    async def fake_attach(workspace, *, repository_id, install_token=None):
        assert repository_id == 92
        return attached

    _patch_dashboard_attr(
        monkeypatch, "attach_github_repository_to_workspace", fake_attach
    )

    async def fake_remember(thread_id, workspace):
        return workspace

    _patch_dashboard_attr(
        monkeypatch, "remember_thread_workspace_ref", fake_remember
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "modal", "repository_id": 92},
    )

    assert response.status_code == 200
    assert response.json()["workspace"]["metadata"]["source"] == "github"


def test_dashboard_workspace_resolve_github_requires_repository_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_attr(
        monkeypatch,
        "create_daytona_workspace",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "github", "backend": "daytona"},
    )

    assert response.status_code == 400
    assert "Repository ID is required" in response.json()["detail"]


def test_dashboard_workspace_resolve_github_daytona_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_create():
        raise ValueError("Daytona API key is not configured.")

    _patch_dashboard_attr(monkeypatch, "create_daytona_workspace", fake_create)
    client = _create_dashboard_client(ChannelManager())

    response = client.post(
        "/dashboard-api/workspace/resolve",
        json={"kind": "github", "backend": "daytona", "repository_id": 1},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Daytona API key is not configured."


@pytest.mark.asyncio
async def test_github_workspace_manager_clones_missing_shared_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_module = importlib.import_module("agent.modules.github.workspace")
    manager = workspace_module.GitHubWorkspaceManager(root=tmp_path)
    calls: list[dict[str, object]] = []

    async def fake_run_git(args, *, cwd, token=None, capture=False):
        calls.append({"args": args, "cwd": cwd, "token": token, "capture": capture})
        if args[0] == "clone":
            Path(args[-1]).mkdir(parents=True)
            (Path(args[-1]) / ".git").mkdir()
        return ""

    monkeypatch.setattr(workspace_module, "_run_git", fake_run_git)

    path = await manager.ensure_shared_checkout(
        full_name="owner/repo",
        token="token-1",
    )

    assert path == tmp_path / "owner" / "repo"
    assert calls == [
        {
            "args": [
                "clone",
                "--origin",
                "origin",
                "https://github.com/owner/repo.git",
                str(tmp_path / "owner" / "repo"),
            ],
            "cwd": tmp_path / "owner",
            "token": "token-1",
            "capture": False,
        }
    ]


@pytest.mark.asyncio
async def test_github_workspace_manager_rejects_non_git_path(tmp_path: Path) -> None:
    workspace_module = importlib.import_module("agent.modules.github.workspace")
    manager = workspace_module.GitHubWorkspaceManager(root=tmp_path)
    (tmp_path / "owner" / "repo").mkdir(parents=True)

    with pytest.raises(ValueError, match="not a Git repository"):
        await manager.ensure_shared_checkout(
            full_name="owner/repo",
            token="token-1",
        )


def test_dashboard_workspace_tree_ignores_noisy_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.js").write_text("ignored\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ignored\n", encoding="utf-8")

    client = _create_dashboard_client(ChannelManager())
    response = client.get(
        "/dashboard-api/workspace/tree",
        params={"backend": "local", "locator": str(tmp_path)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == _absolute(tmp_path)
    names = [entry["name"] for entry in payload["entries"]]
    assert names == ["src"]
    assert payload["entries"][0]["path"] == _absolute(tmp_path / "src")


def test_dashboard_workspace_tree_blocks_directory_escape(tmp_path: Path) -> None:
    client = _create_dashboard_client(ChannelManager())

    response = client.get(
        "/dashboard-api/workspace/tree",
        params={"backend": "local", "locator": str(tmp_path), "path": ".."},
    )

    assert response.status_code == 400
    assert "escapes workspace" in response.json()["detail"]


def test_dashboard_workspace_tree_reports_unavailable_modal_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.modules.workspaces import WorkspaceUnavailableError

    class FakeBackend:
        def tree(self, path: str | None = None):
            del path
            raise WorkspaceUnavailableError(
                (
                    "Modal sandbox sb-expired is no longer available. "
                    "Create or attach a new Modal workspace for this thread."
                ),
                backend="modal",
                locator="sb-expired",
            )

    async def fake_get_workspace_browser(*args, **kwargs):
        del args, kwargs
        return FakeBackend()

    _patch_dashboard_attr(monkeypatch, "get_workspace_browser", fake_get_workspace_browser)
    client = _create_dashboard_client(ChannelManager())

    response = client.get(
        "/dashboard-api/workspace/tree",
        params={
            "thread_id": "thread-1",
            "backend": "modal",
            "locator": "sb-expired",
            "root": "/workspace",
        },
    )

    assert response.status_code == 410
    assert "no longer available" in response.json()["detail"]


def test_dashboard_workspace_file_reads_text_and_blocks_directory_escape(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    client = _create_dashboard_client(ChannelManager())

    response = client.get(
        "/dashboard-api/workspace/file",
        params={"backend": "local", "locator": str(tmp_path), "path": "src/app.py"},
    )
    absolute_response = client.get(
        "/dashboard-api/workspace/file",
        params={
            "backend": "local",
            "locator": str(tmp_path),
            "path": _absolute(tmp_path / "src" / "app.py"),
        },
    )
    escape_response = client.get(
        "/dashboard-api/workspace/file",
        params={"backend": "local", "locator": str(tmp_path), "path": "../outside.py"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == _absolute(tmp_path / "src" / "app.py")
    assert payload["content"].replace("\r\n", "\n") == "print('hello')\n"
    assert payload["binary"] is False
    assert absolute_response.status_code == 200
    assert absolute_response.json()["path"] == _absolute(tmp_path / "src" / "app.py")
    assert escape_response.status_code == 400
    assert "escapes workspace" in escape_response.json()["detail"]


def test_dashboard_workspace_rename_and_delete_return_absolute_paths(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "old.txt"
    new_path = tmp_path / "new.txt"
    old_path.write_text("rename me\n", encoding="utf-8")
    client = _create_dashboard_client(ChannelManager())

    rename_response = client.post(
        "/dashboard-api/workspace/rename",
        json={
            "workspace": _workspace_payload(tmp_path),
            "path": "old.txt",
            "new_name": "new.txt",
        },
    )

    assert rename_response.status_code == 200
    assert rename_response.json()["path"] == _absolute(old_path)
    assert rename_response.json()["new_path"] == _absolute(new_path)
    assert new_path.exists()

    delete_response = client.post(
        "/dashboard-api/workspace/delete",
        json={
            "workspace": _workspace_payload(tmp_path),
            "path": _absolute(new_path),
        },
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["path"] == _absolute(new_path)
    assert delete_response.json()["kind"] == "file"
    assert not new_path.exists()


def test_dashboard_workspace_changes_and_diff_for_git_repo(tmp_path: Path) -> None:
    _require_git()
    repo = tmp_path
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo / "deleted.txt").write_text("remove me\n", encoding="utf-8")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "initial")

    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo / "deleted.txt").unlink()
    (repo / "new.txt").write_text("new file\n", encoding="utf-8")

    client = _create_dashboard_client(ChannelManager())
    response = client.get(
        "/dashboard-api/workspace/changes",
        params={"backend": "local", "locator": str(repo)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_git_repo"] is True
    statuses = {change["path"]: change["status"] for change in payload["changes"]}
    assert statuses == {
        _absolute(repo / "deleted.txt"): "deleted",
        _absolute(repo / "new.txt"): "untracked",
        _absolute(repo / "tracked.txt"): "modified",
    }
    stats = {
        change["path"]: (change["additions"], change["deletions"])
        for change in payload["changes"]
    }
    assert stats == {
        _absolute(repo / "deleted.txt"): (0, 1),
        _absolute(repo / "new.txt"): (1, 0),
        _absolute(repo / "tracked.txt"): (1, 1),
    }

    diff_response = client.get(
        "/dashboard-api/workspace/diff",
        params={"backend": "local", "locator": str(repo), "path": "tracked.txt"},
    )
    assert diff_response.status_code == 200
    diff_payload = diff_response.json()
    assert diff_payload["path"] == _absolute(repo / "tracked.txt")
    diff = diff_payload["diff"]
    assert "-before" in diff
    assert "+after" in diff

    untracked_response = client.get(
        "/dashboard-api/workspace/diff",
        params={
            "backend": "local",
            "locator": str(repo),
            "path": _absolute(repo / "new.txt"),
        },
    )
    assert untracked_response.status_code == 200
    assert untracked_response.json()["path"] == _absolute(repo / "new.txt")
    assert "+++ b/new.txt" in untracked_response.json()["diff"]


def test_dashboard_workspace_changes_for_non_git_workspace() -> None:
    with tempfile.TemporaryDirectory(dir=Path.home()) as temp_dir:
        workspace = Path(temp_dir)
        (workspace / "file.txt").write_text("plain\n", encoding="utf-8")
        client = _create_dashboard_client(ChannelManager())

        changes_response = client.get(
            "/dashboard-api/workspace/changes",
            params={"backend": "local", "locator": str(workspace)},
        )
        diff_response = client.get(
            "/dashboard-api/workspace/diff",
            params={"backend": "local", "locator": str(workspace), "path": "file.txt"},
        )

    assert changes_response.status_code == 200
    assert changes_response.json()["is_git_repo"] is False
    assert changes_response.json()["changes"] == []
    assert diff_response.status_code == 200
    assert diff_response.json()["is_git_repo"] is False
    assert diff_response.json()["path"] == _absolute(workspace / "file.txt")
    assert "not a Git repository" in diff_response.json()["message"]


def test_dashboard_submit_background_task_records_default_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class FakeManager:
        async def submit(self, **kwargs):
            captured.update(kwargs)
            return "task-1"

    _patch_dashboard_attr(monkeypatch, "get_background_task_manager", lambda: FakeManager(),
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.post(
        "/tasks",
        json={"request": "do work", "agent_name": "default"},
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-1"
    assert captured["workspace"].backend == "local"
    assert captured["workspace"].locator


def test_dashboard_chat_history_returns_workspace_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conversations_module = importlib.import_module("agent.modules.conversations")
    workspace = _dashboard_attr("resolve_workspace_ref")(_workspace_payload(tmp_path))
    threads = [
        _conversation_thread("api_dashboard_with", "Thread with workspace"),
        _conversation_thread(
            "task_dashboard_without",
            "Background task without workspace",
            kind="background",
        ),
    ]

    async def fake_list_conversation_threads(
        limit=None,
        offset=0,
        kind=None,
        kinds=None,
    ):
        assert limit is None
        assert offset == 0
        assert kind is None
        assert kinds == ["user", "background"]
        return threads

    async def fake_get_checkpoint_stats(thread_id: str):
        return {"latest_checkpoint_id": f"{thread_id}-checkpoint", "checkpoint_count": 2}

    async def fake_get_thread_workspace_refs(thread_ids: list[str]):
        assert thread_ids == ["api_dashboard_with", "task_dashboard_without"]
        return {"api_dashboard_with": workspace}

    monkeypatch.setattr(
        conversations_module,
        "list_conversation_threads",
        fake_list_conversation_threads,
    )
    _patch_dashboard_attr(monkeypatch, "_get_checkpoint_stats", fake_get_checkpoint_stats)
    _patch_dashboard_attr(monkeypatch, "get_thread_workspace_refs", fake_get_thread_workspace_refs)

    client = _create_dashboard_client(ChannelManager())
    response = client.get("/dashboard-api/chat-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["threads"][0]["workspace"] == workspace.model_dump()
    assert payload["threads"][0]["workspace_key"] == f"local:{workspace.locator}"
    assert payload["threads"][0]["workspace_label"] == workspace.display_label()
    assert payload["threads"][1]["kind"] == "background"
    assert payload["threads"][1]["workspace"] is None
    assert payload["threads"][1]["workspace_key"] == "no-workspace"
    assert payload["threads"][1]["workspace_label"] == "No workspace"


def test_dashboard_chat_history_uses_background_task_workspace_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conversations_module = importlib.import_module("agent.modules.conversations")
    workspace = _dashboard_attr("resolve_workspace_ref")(
        {
            **_workspace_payload(tmp_path),
            "label": "octo/example",
            "metadata": {"source": "github"},
        }
    )
    threads = [
        _conversation_thread(
            "task_dashboard_github",
            "GitHub task",
            kind="background",
        ),
    ]

    class FakeTaskManager:
        def get_by_thread_id(self, thread_id: str):
            assert thread_id == "task_dashboard_github"
            return {"workspace": workspace.model_dump()}

    async def fake_list_conversation_threads(
        limit=None,
        offset=0,
        kind=None,
        kinds=None,
    ):
        assert kind is None
        assert kinds == ["user", "background"]
        return threads

    async def fake_get_checkpoint_stats(thread_id: str):
        return {"latest_checkpoint_id": f"{thread_id}-checkpoint", "checkpoint_count": 1}

    async def fake_get_thread_workspace_refs(thread_ids: list[str]):
        assert thread_ids == ["task_dashboard_github"]
        return {}

    monkeypatch.setattr(
        conversations_module,
        "list_conversation_threads",
        fake_list_conversation_threads,
    )
    _patch_dashboard_attr(monkeypatch, "_get_checkpoint_stats", fake_get_checkpoint_stats)
    _patch_dashboard_attr(monkeypatch, "get_thread_workspace_refs", fake_get_thread_workspace_refs)
    _patch_dashboard_attr(monkeypatch, "get_background_task_manager", lambda: FakeTaskManager(),
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.get("/dashboard-api/chat-history")

    assert response.status_code == 200
    thread = response.json()["threads"][0]
    assert thread["workspace"] == workspace.model_dump()
    assert thread["workspace_key"] == f"local:{workspace.locator}"
    assert thread["workspace_label"] == "octo/example"


def test_dashboard_chat_history_pagination_keeps_workspace_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_module = importlib.import_module("agent.modules.conversations")

    async def fake_list_conversation_threads(
        limit=None,
        offset=0,
        kind=None,
        kinds=None,
    ):
        assert limit == 2
        assert offset == 5
        assert kind is None
        assert kinds == ["user", "background"]
        return [
            _conversation_thread("api_dashboard_page_1", "Page 1"),
            _conversation_thread("api_dashboard_page_2", "Page 2"),
        ]

    async def fake_get_checkpoint_stats(thread_id: str):
        return {"latest_checkpoint_id": f"{thread_id}-checkpoint", "checkpoint_count": 1}

    async def fake_get_thread_workspace_refs(thread_ids: list[str]):
        assert thread_ids == ["api_dashboard_page_1", "api_dashboard_page_2"]
        return {}

    monkeypatch.setattr(
        conversations_module,
        "list_conversation_threads",
        fake_list_conversation_threads,
    )
    _patch_dashboard_attr(monkeypatch, "_get_checkpoint_stats", fake_get_checkpoint_stats)
    _patch_dashboard_attr(monkeypatch, "get_thread_workspace_refs", fake_get_thread_workspace_refs)

    client = _create_dashboard_client(ChannelManager())
    response = client.get(
        "/dashboard-api/chat-history",
        params={"limit": 1, "offset": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_more"] is True
    assert payload["next_offset"] == 6
    assert len(payload["threads"]) == 1
    assert payload["threads"][0]["workspace"] is None
    assert payload["threads"][0]["workspace_key"] == "no-workspace"


def test_dashboard_api_renames_chat_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    conversations_module = importlib.import_module("agent.modules.conversations")

    async def fake_rename(thread_id: str, title: str):
        assert thread_id == "api_dashboard_123"
        assert title == "New title"
        return {
            "thread_id": thread_id,
            "platform": "api",
            "user_id": "dashboard",
            "channel_id": "123",
            "agent_name": "default",
            "title": title,
            "kind": "user",
            "created_at": None,
            "updated_at": None,
        }

    async def fake_stats(thread_id: str):
        assert thread_id == "api_dashboard_123"
        return {"latest_checkpoint_id": "ckpt-1", "checkpoint_count": 2}

    async def fake_workspace_ref_for_thread(thread_id: str, include_default: bool = True):
        assert thread_id == "api_dashboard_123"
        assert include_default is False
        return None

    monkeypatch.setattr(conversations_module, "rename_conversation_thread", fake_rename)
    _patch_dashboard_attr(monkeypatch, "_get_checkpoint_stats", fake_stats)
    _patch_dashboard_attr(monkeypatch, "_workspace_ref_for_thread", fake_workspace_ref_for_thread)

    client = _create_dashboard_client(ChannelManager())
    response = client.patch(
        "/dashboard-api/chat-history/api_dashboard_123",
        json={"title": " New title "},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "New title"
    assert response.json()["checkpoint_count"] == 2
    assert response.json()["workspace_key"] == "no-workspace"


def test_dashboard_background_task_events_streams_snapshot_and_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_module = importlib.import_module("agent.modules.conversations")
    thread_id = "task_dashboard_123"
    task = {
        "task_id": "123",
        "thread_id": thread_id,
        "request": "do work",
        "agent_name": "default",
        "workspace": None,
        "status": "completed",
        "result": "done",
        "error": "",
        "created_at": 1.0,
        "started_at": 2.0,
        "completed_at": 3.0,
        "elapsed_seconds": 1.0,
        "elapsed_display": "1s",
        "notify_channel": None,
    }

    class FakeManager:
        def __init__(self) -> None:
            self.unsubscribed = False

        def get_by_thread_id(self, requested_thread_id: str):
            assert requested_thread_id == thread_id
            return task

        def subscribe(self, requested_thread_id: str):
            assert requested_thread_id == thread_id
            import asyncio

            return asyncio.Queue()

        def unsubscribe(self, requested_thread_id: str, queue) -> None:
            assert requested_thread_id == thread_id
            self.unsubscribed = True

    async def fake_get_conversation_thread(requested_thread_id: str):
        assert requested_thread_id == thread_id
        return {
            "thread_id": thread_id,
            "platform": "task",
            "user_id": "dashboard",
            "channel_id": "123",
            "agent_name": "default",
            "title": "do work",
            "kind": "background",
            "created_at": None,
            "updated_at": None,
        }

    async def fake_get_thread_messages(requested_thread_id: str):
        assert requested_thread_id == thread_id
        return [{"id": "ai-1", "role": "assistant", "content": "done"}]

    manager = FakeManager()
    _patch_dashboard_attr(monkeypatch, "get_background_task_manager", lambda: manager)
    monkeypatch.setattr(
        conversations_module,
        "get_conversation_thread",
        fake_get_conversation_thread,
    )
    _patch_dashboard_attr(monkeypatch, "_get_thread_messages", fake_get_thread_messages)
    _patch_dashboard_attr(monkeypatch, "get_active_session_registry", lambda: SimpleNamespace(list_active=lambda: []),
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.get(
        "/dashboard-api/background-task-events",
        params={"thread_id": thread_id},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: snapshot" in response.text
    assert '"thread_id": "task_dashboard_123"' in response.text
    assert '"role": "assistant"' in response.text
    assert '"task_id": "123"' in response.text
    assert "event: done" in response.text
    assert manager.unsubscribed is True


def test_dashboard_background_task_events_returns_404_for_unknown_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_module = importlib.import_module("agent.modules.conversations")

    class FakeManager:
        def get_by_thread_id(self, thread_id: str):
            return None

        def subscribe(self, thread_id: str):
            raise AssertionError("Unknown threads should not be subscribed.")

    async def fake_get_conversation_thread(thread_id: str):
        return None

    _patch_dashboard_attr(monkeypatch, "get_background_task_manager", lambda: FakeManager(),
    )
    monkeypatch.setattr(
        conversations_module,
        "get_conversation_thread",
        fake_get_conversation_thread,
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.get(
        "/dashboard-api/background-task-events",
        params={"thread_id": "task_dashboard_missing"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Background task thread 'task_dashboard_missing' not found."
    )


@pytest.mark.asyncio
async def test_dashboard_delete_chat_thread_deletes_workflow_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations_module = importlib.import_module("agent.modules.conversations")
    shell_manager_module = importlib.import_module(
        "agent.modules.tools.langchain.shell_tools.session_manager"
    )
    workflows_module = importlib.import_module("agent.modules.workflows")
    workspaces_module = importlib.import_module("agent.modules.workspaces")
    calls: list[tuple[str, str]] = []

    class FakeSessionManager:
        def close_thread_sessions(self, thread_id: str) -> int:
            calls.append(("close_shell", thread_id))
            return 1

    async def fake_mark_deleted(thread_id: str) -> bool:
        calls.append(("mark_deleted", thread_id))
        return True

    async def fake_delete_tree(thread_id: str) -> None:
        calls.append(("delete_tree", thread_id))

    async def fake_delete_workspace(thread_id: str) -> None:
        calls.append(("delete_workspace", thread_id))

    monkeypatch.setattr(
        conversations_module,
        "mark_conversation_thread_deleted",
        fake_mark_deleted,
    )
    monkeypatch.setattr(shell_manager_module, "session_manager", FakeSessionManager())
    monkeypatch.setattr(
        workflows_module,
        "delete_workflow_thread_tree",
        fake_delete_tree,
    )
    monkeypatch.setattr(
        workspaces_module,
        "delete_thread_workspace",
        fake_delete_workspace,
    )

    result = await _dashboard_attr("delete_chat_thread")("api_dashboard_123")

    assert result == {"status": "deleted", "thread_id": "api_dashboard_123"}
    assert calls == [
        ("close_shell", "api_dashboard_123"),
        ("delete_workspace", "api_dashboard_123"),
        ("mark_deleted", "api_dashboard_123"),
        ("delete_tree", "api_dashboard_123"),
    ]


@pytest.mark.asyncio
async def test_dashboard_delete_background_task_thread_deletes_workflow_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_runtime_module = importlib.import_module("agent.modules.agent_runtime")
    conversations_module = importlib.import_module("agent.modules.conversations")
    shell_manager_module = importlib.import_module(
        "agent.modules.tools.langchain.shell_tools.session_manager"
    )
    workflows_module = importlib.import_module("agent.modules.workflows")
    workspaces_module = importlib.import_module("agent.modules.workspaces")
    calls: list[tuple[str, str]] = []

    class FakeSessionManager:
        def close_thread_sessions(self, thread_id: str) -> int:
            calls.append(("close_shell", thread_id))
            return 1

    class FakeBackgroundTaskRepository:
        async def mark_deleted_by_thread_id(self, thread_id: str) -> bool:
            calls.append(("mark_task_deleted", thread_id))
            return True

    async def fake_mark_deleted(thread_id: str) -> bool:
        calls.append(("mark_thread_deleted", thread_id))
        return True

    async def fake_delete_tree(thread_id: str) -> None:
        calls.append(("delete_tree", thread_id))

    async def fake_delete_workspace(thread_id: str) -> None:
        calls.append(("delete_workspace", thread_id))

    monkeypatch.setattr(
        agent_runtime_module,
        "get_background_task_repository",
        lambda: FakeBackgroundTaskRepository(),
    )
    monkeypatch.setattr(
        conversations_module,
        "mark_conversation_thread_deleted",
        fake_mark_deleted,
    )
    monkeypatch.setattr(shell_manager_module, "session_manager", FakeSessionManager())
    monkeypatch.setattr(
        workflows_module,
        "delete_workflow_thread_tree",
        fake_delete_tree,
    )
    monkeypatch.setattr(
        workspaces_module,
        "delete_thread_workspace",
        fake_delete_workspace,
    )

    result = await _dashboard_attr("delete_background_task_thread")(
        "task_dashboard_123"
    )

    assert result == {"status": "deleted", "thread_id": "task_dashboard_123"}
    assert calls == [
        ("close_shell", "task_dashboard_123"),
        ("delete_workspace", "task_dashboard_123"),
        ("mark_task_deleted", "task_dashboard_123"),
        ("mark_thread_deleted", "task_dashboard_123"),
        ("delete_tree", "task_dashboard_123"),
    ]


def _checkpoint_tuple(
    checkpoint_id: str,
    parent_id: str | None,
    messages: list,
    *,
    ts: str,
):
    return SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "api_dashboard_123",
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        },
        parent_config={
            "configurable": {
                "thread_id": "api_dashboard_123",
                "checkpoint_ns": "",
                "checkpoint_id": parent_id,
            }
        }
        if parent_id
        else None,
        checkpoint={
            "id": checkpoint_id,
            "ts": ts,
            "channel_values": {"messages": messages},
        },
    )


@pytest.mark.asyncio
async def test_dashboard_thread_messages_include_checkpoint_branch_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    checkpoints = [
        _checkpoint_tuple("c0", None, [], ts="2026-01-01T00:00:00Z"),
        _checkpoint_tuple(
            "c1",
            "c0",
            [HumanMessage(content="original", id="h1")],
            ts="2026-01-01T00:00:01Z",
        ),
        _checkpoint_tuple(
            "c2",
            "c1",
            [
                HumanMessage(content="original", id="h1"),
                AIMessage(content="first", id="a1"),
            ],
            ts="2026-01-01T00:00:02Z",
        ),
        _checkpoint_tuple(
            "c3",
            "c0",
            [HumanMessage(content="edited", id="h1")],
            ts="2026-01-01T00:00:03Z",
        ),
        _checkpoint_tuple(
            "c4",
            "c3",
            [
                HumanMessage(content="edited", id="h1"),
                AIMessage(content="second", id="a2"),
            ],
            ts="2026-01-01T00:00:04Z",
        ),
    ]

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            checkpoint_id = config["configurable"].get("checkpoint_id") or "c4"
            return next(
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.config["configurable"]["checkpoint_id"] == checkpoint_id
            )

        async def alist(self, config):
            for checkpoint in reversed(checkpoints):
                yield checkpoint

    _patch_dashboard_attr(monkeypatch, "_get_checkpointer", lambda: FakeCheckpointer())

    messages, active_checkpoint_id = await _dashboard_attr("_get_thread_messages_payload")(
        "api_dashboard_123",
        include_branch_metadata=True,
    )

    assert active_checkpoint_id == "c4"
    user_message = messages[0]
    assert user_message["content"] == "edited"
    assert user_message["message_index"] == 0
    assert user_message["source_checkpoint_id"] == "c3"
    assert user_message["parent_checkpoint_id"] == "c0"
    assert user_message["branch"] == {
        "current": 2,
        "total": 2,
        "options": [
            {"checkpoint_id": "c2", "message": "original"},
            {"checkpoint_id": "c4", "message": "edited"},
        ],
    }


@pytest.mark.asyncio
async def test_dashboard_thread_messages_can_load_specific_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    checkpoints = [
        _checkpoint_tuple("c0", None, [], ts="2026-01-01T00:00:00Z"),
        _checkpoint_tuple(
            "c1",
            "c0",
            [HumanMessage(content="original", id="h1")],
            ts="2026-01-01T00:00:01Z",
        ),
        _checkpoint_tuple(
            "c2",
            "c1",
            [
                HumanMessage(content="original", id="h1"),
                AIMessage(content="first", id="a1"),
            ],
            ts="2026-01-01T00:00:02Z",
        ),
        _checkpoint_tuple(
            "c3",
            "c0",
            [HumanMessage(content="edited", id="h1")],
            ts="2026-01-01T00:00:03Z",
        ),
        _checkpoint_tuple(
            "c4",
            "c3",
            [
                HumanMessage(content="edited", id="h1"),
                AIMessage(content="second", id="a2"),
            ],
            ts="2026-01-01T00:00:04Z",
        ),
    ]

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            checkpoint_id = config["configurable"].get("checkpoint_id") or "c4"
            return next(
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.config["configurable"]["checkpoint_id"] == checkpoint_id
            )

        async def alist(self, config):
            for checkpoint in reversed(checkpoints):
                yield checkpoint

    _patch_dashboard_attr(monkeypatch, "_get_checkpointer", lambda: FakeCheckpointer())

    messages, active_checkpoint_id = await _dashboard_attr("_get_thread_messages_payload")(
        "api_dashboard_123",
        checkpoint_id="c2",
        include_branch_metadata=True,
    )

    assert active_checkpoint_id == "c2"
    assert messages[0]["content"] == "original"
    assert messages[0]["branch"]["current"] == 1
    assert messages[1]["content"] == "first"


@pytest.mark.asyncio
async def test_dashboard_branch_metadata_only_marks_changed_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    first_user = HumanMessage(content="first", id="h1")
    first_ai = AIMessage(content="first response", id="a1")
    checkpoints = [
        _checkpoint_tuple("c0", None, [], ts="2026-01-01T00:00:00Z"),
        _checkpoint_tuple("c1", "c0", [first_user], ts="2026-01-01T00:00:01Z"),
        _checkpoint_tuple("c2", "c1", [first_user, first_ai], ts="2026-01-01T00:00:02Z"),
        _checkpoint_tuple(
            "c3",
            "c2",
            [
                first_user,
                first_ai,
                HumanMessage(content="second original", id="h2"),
            ],
            ts="2026-01-01T00:00:03Z",
        ),
        _checkpoint_tuple(
            "c4",
            "c3",
            [
                first_user,
                first_ai,
                HumanMessage(content="second original", id="h2"),
                AIMessage(content="old second response", id="a2"),
            ],
            ts="2026-01-01T00:00:04Z",
        ),
        _checkpoint_tuple(
            "c5",
            "c2",
            [
                first_user,
                first_ai,
                HumanMessage(content="second edited", id="h2"),
            ],
            ts="2026-01-01T00:00:05Z",
        ),
        _checkpoint_tuple(
            "c6",
            "c5",
            [
                first_user,
                first_ai,
                HumanMessage(content="second edited", id="h2"),
                AIMessage(content="new second response", id="a3"),
            ],
            ts="2026-01-01T00:00:06Z",
        ),
    ]

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            checkpoint_id = config["configurable"].get("checkpoint_id") or "c6"
            return next(
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.config["configurable"]["checkpoint_id"] == checkpoint_id
            )

        async def alist(self, config):
            for checkpoint in reversed(checkpoints):
                yield checkpoint

    _patch_dashboard_attr(monkeypatch, "_get_checkpointer", lambda: FakeCheckpointer())

    messages, _ = await _dashboard_attr("_get_thread_messages_payload")(
        "api_dashboard_123",
        include_branch_metadata=True,
    )

    assert messages[0]["content"] == "first"
    assert "branch" not in messages[0]
    assert messages[2]["content"] == "second edited"
    assert messages[2]["branch"] == {
        "current": 2,
        "total": 2,
        "options": [
            {"checkpoint_id": "c4", "message": "second original"},
            {"checkpoint_id": "c6", "message": "second edited"},
        ],
    }


@pytest.mark.asyncio
async def test_dashboard_thread_messages_preserve_assistant_text_with_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import AIMessage, ToolMessage

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            assert config == {
                "configurable": {
                    "thread_id": "api_dashboard_123",
                    "checkpoint_ns": "",
                }
            }
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            AIMessage(
                                content="I will inspect the files.",
                                id="ai-tool",
                                tool_calls=[
                                    {
                                        "id": "call-1",
                                        "name": "list_dir",
                                        "args": {"path": "."},
                                    }
                                ],
                            ),
                            ToolMessage(
                                content="README.md",
                                tool_call_id="call-1",
                                name="list_dir",
                                id="tool-result",
                            ),
                        ]
                    }
                }
            )

    _patch_dashboard_attr(monkeypatch, "_get_checkpointer", lambda: FakeCheckpointer(),
    )

    messages = await _dashboard_attr("_get_thread_messages")("api_dashboard_123")

    assert messages == [
        {
            "id": "ai-tool",
            "role": "assistant",
            "content": "I will inspect the files.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "list_dir",
                    "args": {"path": "."},
                }
            ],
        },
        {
            "id": "tool-result",
            "role": "tool",
            "name": "list_dir",
            "tool_call_id": "call-1",
            "content": "README.md",
        },
    ]


@pytest.mark.asyncio
async def test_dashboard_thread_messages_hide_raw_image_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.messages import HumanMessage

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            assert config == {
                "configurable": {
                    "thread_id": "api_dashboard_123",
                    "checkpoint_ns": "",
                }
            }
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            HumanMessage(
                                content=[
                                    {"type": "text", "text": "Review attachments"},
                                    {
                                        "type": "text",
                                        "text": "Attached image: screen.png",
                                    },
                                    {
                                        "type": "image",
                                        "base64": "raw-image-data",
                                        "mime_type": "image/png",
                                    },
                                ],
                                id="human-attachment",
                                additional_kwargs={
                                    "attachments": [
                                        {
                                            "name": "screen.png",
                                            "mime_type": "image/png",
                                            "size": 14,
                                            "kind": "image",
                                        }
                                    ]
                                },
                            ),
                        ]
                    }
                }
            )

    _patch_dashboard_attr(monkeypatch, "_get_checkpointer", lambda: FakeCheckpointer(),
    )

    messages = await _dashboard_attr("_get_thread_messages")("api_dashboard_123")

    assert messages == [
        {
            "id": "human-attachment",
            "role": "user",
            "content": "Review attachments",
            "attachments": [
                {
                    "name": "screen.png",
                    "mime_type": "image/png",
                    "size": 14,
                    "kind": "image",
                }
            ],
        }
    ]
    assert "raw-image-data" not in str(messages)


def test_dashboard_api_github_returns_repository_bindings(monkeypatch: pytest.MonkeyPatch) -> None:

    class FakeService:
        async def list_repository_bindings(self):
            return [{"repository_id": 100, "full_name": "octo/example"}]

    class FakeCatalog:
        def list_agent_cards(self):
            return [SimpleNamespace(name="default", valid=True)]

    _patch_dashboard_attr(monkeypatch, "get_github_settings", lambda: SimpleNamespace(
            is_configured=True,
            enabled=True,
            app_slug="kaka-agent",
            default_agent="default",
            trigger_label="kaka-agent",
            mention_triggers=("@kaka-agent", "/kaka"),
        ),
    )
    _patch_dashboard_attr(monkeypatch, "get_github_automation_service", lambda: FakeService(),
    )
    _patch_dashboard_attr(monkeypatch, "get_catalog_service", lambda: FakeCatalog(),
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.get("/dashboard-api/github")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["install_url"] == "https://github.com/apps/kaka-agent/installations/new"
    assert data["repositories"] == [{"repository_id": 100, "full_name": "octo/example"}]
    assert data["agent_names"] == ["default"]


def test_dashboard_api_github_repository_detail_and_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeService:
        def __init__(self) -> None:
            self.submissions: list[dict[str, object]] = []
            self.updates: list[dict[str, object]] = []

        async def get_repository_binding(self, repository_id: int):
            if repository_id != 100:
                raise KeyError("missing")
            return {
                "repository_id": 100,
                "installation_id": 10,
                "full_name": "octo/example",
                "enabled": True,
                "agent_name": "default",
                "trigger_label": "kaka-agent",
                "mention_triggers": ["@kaka-agent"],
                "allowed_tools": ["read_file"],
                "allowed_skills": ["code-style"],
            }

        async def submit_repository_task(self, repository_id: int, **kwargs):
            self.submissions.append({"repository_id": repository_id, **kwargs})
            return "task-1"

        async def update_repository_binding(self, repository_id: int, **kwargs):
            self.updates.append({"repository_id": repository_id, **kwargs})
            return {"repository_id": repository_id, **kwargs}

    fake_service = FakeService()

    async def fake_agent_options():
        return {
            "agent_names": ["default"],
            "tools": ["read_file"],
            "tool_groups": [{"category": "file", "tools": ["read_file"]}],
            "provider_names": ["main"],
            "default_provider": "main",
            "default_model": "model",
            "model_catalogs": [],
            "model_catalog_error": "",
        }

    async def fake_identities():
        return []

    _patch_dashboard_attr(monkeypatch, "get_github_automation_service", lambda: fake_service)
    _patch_dashboard_attr(monkeypatch, "_agent_card_options", fake_agent_options)
    _patch_dashboard_attr(monkeypatch, "_paired_identities", fake_identities)
    _patch_dashboard_attr(
        monkeypatch,
        "list_available_skills",
        lambda: [
            SimpleNamespace(
                name="code-style",
                description="Code style instructions.",
                path=Path("skills/code-style"),
            )
        ],
    )
    _patch_dashboard_attr(monkeypatch, "get_repository_skill_dir", lambda: ".agent/skills")

    client = _create_dashboard_client(ChannelManager())
    response = client.get("/dashboard-api/github/repositories/100")

    assert response.status_code == 200
    data = response.json()
    assert data["repository"]["full_name"] == "octo/example"
    assert data["repository"]["allowed_skills"] == ["code-style"]
    assert data["agent_names"] == ["default"]
    assert data["tools"] == ["read_file"]
    assert data["skills"][0]["name"] == "code-style"
    assert data["repository_skill_dir"] == ".agent/skills"

    missing = client.get("/dashboard-api/github/repositories/101")
    assert missing.status_code == 404

    updated = client.put(
        "/dashboard-api/github/repositories/100/binding",
        json={
            "enabled": True,
            "agent_name": "default",
            "trigger_label": "repo-agent",
            "mention_triggers": ["@repo"],
            "issue_label_enabled": False,
            "issue_comment_enabled": True,
            "pr_review_comment_enabled": False,
            "repository_instructions": "Run tests first.",
            "provider_name": "main",
            "model_name": "model",
            "context_trim_threshold": 24000,
            "tool_policy_mode": "custom",
            "allowed_tools": ["read_file"],
            "allowed_skills": ["code-style"],
            "branch_prefix": "repo-bot",
        },
    )
    assert updated.status_code == 200
    assert fake_service.updates[0]["repository_instructions"] == "Run tests first."
    assert fake_service.updates[0]["allowed_tools"] == ["read_file"]
    assert fake_service.updates[0]["allowed_skills"] == ["code-style"]

    submitted = client.post(
        "/dashboard-api/github/repositories/100/tasks",
        json={"request": "Fix build", "notify_platform": "telegram", "notify_external_id": "123"},
    )
    assert submitted.status_code == 200
    assert submitted.json() == {"status": "submitted", "task_id": "task-1"}
    assert fake_service.submissions == [
        {
            "repository_id": 100,
            "request": "Fix build",
            "notify_platform": "telegram",
            "notify_external_id": "123",
            "notify_channel_id": "",
        }
    ]


def test_dashboard_api_requires_json_auth() -> None:
    app = FastAPI()
    app.state.channel_manager = ChannelManager()
    app.include_router(dashboard_router)
    client = TestClient(app)

    response = client.get("/dashboard-api/session")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_login_route_serves_public_spa_index() -> None:
    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app)

    response = client.get("/login")

    assert response.status_code == 200
    assert '<div id="root">' in response.text


def test_dashboard_assets_are_public_when_built() -> None:
    asset = next((STATIC_DIR / "assets").glob("*.js"))
    app = FastAPI()
    app.mount(
        "/dashboard-assets",
        StaticFiles(directory=STATIC_DIR, check_dir=False),
        name="dashboard-assets",
    )
    client = TestClient(app)

    response = client.get(f"/dashboard-assets/assets/{asset.name}")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_login_json_sets_admin_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_router_module = importlib.import_module("agent.delivery.http.dashboard.auth_router")

    class Admin:
        id = 123

    class FakeAuthService:
        async def authenticate(self, password: str):
            return Admin() if password == "secret" else None

    monkeypatch.setattr(
        auth_router_module,
        "get_admin_auth_service",
        lambda: FakeAuthService(),
    )
    monkeypatch.setattr(auth_router_module, "create_access_token", lambda _: "token-value")

    app = FastAPI()
    app.include_router(auth_router_module.router)
    client = TestClient(app)

    response = client.post(
        "/login",
        json={"password": "secret"},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    assert response.cookies.get("admin_token") == "token-value"


@pytest.mark.asyncio
async def test_create_scheduler_job_accepts_relative_trigger(monkeypatch) -> None:
    from agent.modules.scheduler import triggers as trigger_module
    scheduler = FakeScheduler()
    now = datetime(2026, 4, 13, 22, 30, 0, tzinfo=scheduler.timezone)
    _patch_dashboard_attr(monkeypatch, "_get_scheduler", lambda: scheduler)
    monkeypatch.setattr(trigger_module, "_scheduler_now", lambda _: now)

    body = _dashboard_attr("CreateJobBody")(
        task="eat",
        platform="telegram",
        user_id="123",
        trigger_type="relative",
        trigger_args={"minutes": 2},
    )

    result = await _dashboard_attr("create_scheduler_job")(body)

    assert result["status"] == "created"
    assert result["job"]["trigger_type"] == "date"
    assert scheduler.calls[0]["trigger"] == "date"
    assert scheduler.calls[0]["trigger_args"]["run_date"] == now + timedelta(minutes=2)


@pytest.mark.asyncio
async def test_run_scheduler_job_now_queues_existing_job(monkeypatch) -> None:
    scheduler = FakeScheduler()
    job = FakeJob(
        DateTrigger(),
        {"platform": "telegram", "user_id": "123", "task": "eat"},
        datetime(2026, 4, 14, 9, 0, 0, tzinfo=scheduler.timezone),
    )
    scheduler.jobs[job.id] = job
    _patch_dashboard_attr(monkeypatch, "_get_scheduler", lambda: scheduler)

    background_tasks = BackgroundTasks()
    result = await _dashboard_attr("run_scheduler_job_now")(job.id, background_tasks)

    assert result == {"status": "queued", "job_id": job.id}
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is _dashboard_attr("execute_scheduled_task")
    assert task.kwargs == {"platform": "telegram", "user_id": "123", "task": "eat"}
