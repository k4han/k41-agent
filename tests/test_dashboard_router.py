import importlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.delivery.http.dashboard.router import _collection_payload, router as dashboard_router
from agent.modules.admin_auth.public import get_current_admin
from agent.modules.channels.public import ChannelManager


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


def test_collection_payload_returns_services_only() -> None:
    channel_manager = ChannelManager()
    channel_manager.register("telegram", idle_runner)
    channel_manager.register("discord", idle_runner)

    payload = _collection_payload(channel_manager)

    assert payload == {
        "services": [
            {"name": "telegram", "status": "stopped", "error": None},
            {"name": "discord", "status": "stopped", "error": None},
        ]
    }
    assert "bots" not in payload


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


def test_scheduler_template_uses_safe_job_action_buttons() -> None:
    dashboard_router_module = importlib.import_module("agent.delivery.http.dashboard.router")
    template = dashboard_router_module.templates.get_template("scheduler.html")

    html = template.render(
        jobs=[
            {
                "id": 'job-"quoted"',
                "task": "don't break buttons",
                "platform": "telegram",
                "user_id": "123",
                "trigger_type": "date",
                "next_run_time": "2026-04-14 09:00:00 +07",
                "paused": False,
            }
        ],
        identities=[],
        scheduler_timezone="Asia/Bangkok",
    )

    assert 'data-job-action="run"' in html
    assert 'id="scheduler-jobs-data"' in html
    assert 'onclick="runJobNow' not in html
    assert 'onclick="openEditModal' not in html


@pytest.mark.asyncio
async def test_create_scheduler_job_accepts_relative_trigger(monkeypatch) -> None:
    from agent.modules.scheduler.infrastructure import triggers as trigger_module

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
