import importlib
from datetime import datetime, timedelta
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
    app = FastAPI()
    app.state.channel_manager = channel_manager
    app.include_router(dashboard_router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app)


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


def test_dashboard_api_overview_returns_runtime_snapshot() -> None:
    channel_manager = ChannelManager()
    channel_manager.register("telegram", idle_runner)

    client = _create_dashboard_client(channel_manager)
    response = client.get("/dashboard-api/overview")

    assert response.status_code == 200
    assert response.json() == {
        "services": [{"name": "telegram", "status": "stopped", "error": None}]
    }


def test_dashboard_api_github_returns_repository_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    dashboard_router_module = importlib.import_module("agent.delivery.http.dashboard.router")

    class FakeService:
        async def list_repository_bindings(self):
            return [{"repository_id": 100, "full_name": "octo/example"}]

    class FakeCatalog:
        def list_agent_cards(self):
            return [SimpleNamespace(name="default", valid=True)]

    monkeypatch.setattr(
        dashboard_router_module,
        "get_github_settings",
        lambda: SimpleNamespace(
            is_configured=True,
            enabled=True,
            app_slug="kaka-agent",
            default_agent="default",
            trigger_label="kaka-agent",
            mention_triggers=("@kaka-agent", "/kaka"),
        ),
    )
    monkeypatch.setattr(
        dashboard_router_module,
        "get_github_automation_service",
        lambda: FakeService(),
    )
    monkeypatch.setattr(
        dashboard_router_module,
        "get_catalog_service",
        lambda: FakeCatalog(),
    )

    client = _create_dashboard_client(ChannelManager())
    response = client.get("/dashboard-api/github")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["install_url"] == "https://github.com/apps/kaka-agent/installations/new"
    assert data["repositories"] == [{"repository_id": 100, "full_name": "octo/example"}]
    assert data["agent_names"] == ["default"]


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

    dashboard_router_module = importlib.import_module("agent.delivery.http.dashboard.router")
    scheduler = FakeScheduler()
    now = datetime(2026, 4, 13, 22, 30, 0, tzinfo=scheduler.timezone)
    monkeypatch.setattr(dashboard_router_module, "_get_scheduler", lambda: scheduler)
    monkeypatch.setattr(trigger_module, "_scheduler_now", lambda _: now)

    body = dashboard_router_module.CreateJobBody(
        task="eat",
        platform="telegram",
        user_id="123",
        trigger_type="relative",
        trigger_args={"minutes": 2},
    )

    result = await dashboard_router_module.create_scheduler_job(body)

    assert result["status"] == "created"
    assert result["job"]["trigger_type"] == "date"
    assert scheduler.calls[0]["trigger"] == "date"
    assert scheduler.calls[0]["trigger_args"]["run_date"] == now + timedelta(minutes=2)


@pytest.mark.asyncio
async def test_run_scheduler_job_now_queues_existing_job(monkeypatch) -> None:
    dashboard_router_module = importlib.import_module("agent.delivery.http.dashboard.router")
    scheduler = FakeScheduler()
    job = FakeJob(
        DateTrigger(),
        {"platform": "telegram", "user_id": "123", "task": "eat"},
        datetime(2026, 4, 14, 9, 0, 0, tzinfo=scheduler.timezone),
    )
    scheduler.jobs[job.id] = job
    monkeypatch.setattr(dashboard_router_module, "_get_scheduler", lambda: scheduler)

    background_tasks = BackgroundTasks()
    result = await dashboard_router_module.run_scheduler_job_now(job.id, background_tasks)

    assert result == {"status": "queued", "job_id": job.id}
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is dashboard_router_module.execute_scheduled_task
    assert task.kwargs == {"platform": "telegram", "user_id": "123", "task": "eat"}
