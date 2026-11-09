from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agent.shared.infrastructure.db import Base, load_orm_models
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine


@pytest_asyncio.fixture
async def history_db(tmp_path, request, monkeypatch):
    await close_async_engine()

    db_path = tmp_path / f"{request.node.name}.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"

    from agent.bootstrap.container import create_test_container, set_active_container

    set_active_container(create_test_container(database_url=db_url))

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)
    import agent.modules.workspaces.service as workspace_service
    monkeypatch.setattr(workspace_service, "temp_workspace_root", lambda: tmp_path / "sessions")

    try:
        yield
    finally:
        await close_async_engine()


def _patch_execution(monkeypatch, *, response: str | None = "done", error: Exception | None = None):
    import agent.modules.conversations as conversations_module
    from agent.modules.scheduler import service as scheduler_service

    async def fake_run_agent_full(**kwargs):
        if error is not None:
            raise error
        return response

    async def fake_noop(*args, **kwargs):
        return None

    async def fake_send_notification(*args, **kwargs):
        return True

    monkeypatch.setattr(scheduler_service, "run_agent_full", fake_run_agent_full)
    monkeypatch.setattr(scheduler_service, "_send_notification", fake_send_notification)
    monkeypatch.setattr(
        scheduler_service,
        "describe_executor_model",
        lambda: {"agent_name": "scheduler-executor", "provider": "openai-main", "model": "gpt-test",
                 "configured": "default", "error": ""},
    )
    monkeypatch.setattr(conversations_module, "inject_agent_message_pair", fake_noop)


@pytest_asyncio.fixture
async def scheduled_projects(history_db, tmp_path):
    from agent.bootstrap.container import get_active_container
    from agent.modules.projects import add_project
    from agent.modules.projects.storage import LocalProjectStorage
    from agent.modules.projects.workspaces import ProjectWorkspaceService
    from agent.modules.workspaces import WorkspaceRef

    container = get_active_container()
    container._persistence_ready = True
    service = ProjectWorkspaceService(container, storage=LocalProjectStorage(tmp_path / "storage"))
    service.runtime_root = tmp_path / "runtimes"
    container._project_workspace_service = service
    projects = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        projects.append(await add_project(WorkspaceRef(locator=str(root), label=name)))
    try:
        yield container, service, projects
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_repeated_runs_have_private_workspaces_and_do_not_touch_user_thread(history_db, monkeypatch):
    from sqlalchemy import select
    from agent.modules.conversations import ConversationThread, upsert_conversation_thread
    from agent.modules.conversations.navigation import list_conversation_projects
    from agent.modules.scheduler import service
    from agent.modules.scheduler.history import list_runs
    from agent.modules.workspaces import get_thread_workspace_ref
    from agent.shared.infrastructure.db.session import get_async_session

    _patch_execution(monkeypatch)
    import agent.modules.conversations as conversations
    inject = AsyncMock(side_effect=AssertionError("Shared user history must not be changed."))
    monkeypatch.setattr(conversations, "inject_agent_message_pair", inject)
    await upsert_conversation_thread(thread_id="existing-user", title="Original title", kind="user")
    calls = []

    async def run(**kwargs):
        calls.append(kwargs)
        root = Path(kwargs["workspace"].locator)
        assert not (root / "result.txt").exists()
        (root / "result.txt").write_text(str(len(calls)), encoding="utf-8")
        return f"result-{len(calls)}"

    monkeypatch.setattr(service, "run_agent_full", run)
    for source in ("schedule", "manual"):
        await service.execute_scheduled_task("telegram", "123", "same task", scheduler_job_id="job", trigger_source=source)
    runs = (await list_runs(job_id="job"))["runs"]
    assert len({item["run_id"] for item in runs}) == 2
    assert len({item["thread_id"] for item in runs}) == 2
    assert {item["response"] for item in runs} == {"result-1", "result-2"}
    assert len({item["workspace"].locator for item in calls}) == 2
    for call in calls:
        assert (await get_thread_workspace_ref(call["thread_id"])).metadata["no_workspace"] is True
    inject.assert_not_awaited()
    session = await get_async_session()
    async with session:
        threads = list((await session.execute(select(ConversationThread))).scalars())
    assert len(threads) == 3
    assert sum(thread.kind == "scheduled" for thread in threads) == 2
    assert next(thread for thread in threads if thread.thread_id == "existing-user").title == "Original title"
    groups = await list_conversation_projects()
    assert [item["thread_id"] for group in groups["projects"] for item in group["threads"]] == ["existing-user"]


@pytest.mark.asyncio
async def test_job_project_changes_apply_only_to_future_runs(scheduled_projects, monkeypatch):
    import asyncio
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from fastapi import BackgroundTasks
    from agent.delivery.http.dashboard.routes.scheduler import (
        CreateJobBody, UpdateJobBody, create_scheduler_job, update_scheduler_job, run_scheduler_job_now,
    )
    from agent.modules.scheduler.history import list_runs
    from agent.modules.workspaces import get_thread_workspace_ref

    container, _, projects = scheduled_projects
    _patch_execution(monkeypatch)
    scheduler = AsyncIOScheduler()
    container._scheduler = scheduler
    scheduler.start(paused=True)
    try:
        result = await create_scheduler_job(CreateJobBody(
            task="project task", platform="telegram", user_id="123", trigger_type="interval",
            trigger_args={"hours": 1}, project_id=projects[0]["id"],
        ), admin="1")
        job = scheduler.get_job(result["job"]["id"])
        assert result["job"]["project_id"] == projects[0]["id"]
        manual = BackgroundTasks()
        await run_scheduler_job_now(job.id, manual, admin="1")
        assert manual.tasks[0].kwargs["project_id"] == projects[0]["id"]
        await manual()
        first = (await list_runs(job_id=job.id))["runs"][0]
        await update_scheduler_job(job.id, UpdateJobBody(task="updated task"), admin="1")
        assert job.kwargs["project_id"] == projects[0]["id"]
        await update_scheduler_job(job.id, UpdateJobBody(project_id=projects[1]["id"]), admin="1")
        await job.func(**job.kwargs)
        second = (await list_runs(job_id=job.id))["runs"][0]
        await update_scheduler_job(job.id, UpdateJobBody(project_id=None), admin="1")
        assert job.kwargs["project_id"] is None
        await job.func(**job.kwargs)
        third = (await list_runs(job_id=job.id))["runs"][0]
        assert (await get_thread_workspace_ref(first["thread_id"])).locator == projects[0]["workspace"]["execution"]["locator"]
        assert (await get_thread_workspace_ref(second["thread_id"])).locator == projects[1]["workspace"]["execution"]["locator"]
        assert (await get_thread_workspace_ref(third["thread_id"])).metadata["no_workspace"] is True
        assert {item["status"] for item in (await list_runs(job_id=job.id))["runs"]} == {"completed"}
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)
        container._scheduler = None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "archived", "migration", "retired"])
async def test_unavailable_project_fails_without_fallback(scheduled_projects, monkeypatch, failure):
    from sqlalchemy import update
    from agent.modules.projects import ProjectWorkspaceError, update_project
    from agent.modules.projects.models import Project, WorkspaceRuntime
    from agent.modules.scheduler import service
    from agent.modules.scheduler.history import list_runs
    from agent.shared.infrastructure.db.session import get_async_session

    _, _, projects = scheduled_projects
    project_id = projects[0]["id"]
    if failure == "missing":
        project_id = 999999
    elif failure == "archived":
        await update_project(project_id, {"status": "archived"})
    else:
        session = await get_async_session()
        async with session:
            if failure == "migration":
                await session.execute(update(Project).where(Project.id == project_id).values(migration_status="needs_selection"))
            else:
                await session.execute(update(WorkspaceRuntime).where(
                    WorkspaceRuntime.project_workspace_id == projects[0]["workspace"]["project_workspace_id"]
                ).values(status="retired"))
            await session.commit()
    with pytest.raises((KeyError, ValueError, ProjectWorkspaceError)):
        await service.resolve_scheduled_project(project_id)
    _patch_execution(monkeypatch)
    run = AsyncMock()
    monkeypatch.setattr(service, "run_agent_full", run)
    await service.execute_scheduled_task("telegram", "123", "task", project_id=project_id)
    run.assert_not_awaited()
    record = (await list_runs())["runs"][0]
    assert record["status"] == "failed"
    assert record["error"]


@pytest.mark.asyncio
async def test_schedule_tool_requires_explicit_project_selection(scheduled_projects, monkeypatch):
    import asyncio
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from agent.modules.tools.builtin.schedule.schedule import schedule_task

    container, _, projects = scheduled_projects
    scheduler = AsyncIOScheduler()
    container._scheduler = scheduler
    scheduler.start(paused=True)
    runtime = SimpleNamespace(config={
        "configurable": {"thread_id": "project-conversation", "project_id": projects[0]["id"]},
        "metadata": {"usage_context": {"platform": "telegram", "user_id": "123"}},
    })
    try:
        await schedule_task.coroutine("outside", "interval", {"hours": 1}, runtime)
        assert scheduler.get_jobs()[0].kwargs["project_id"] is None
        await schedule_task.coroutine("inside", "interval", {"hours": 1}, runtime, project_id=projects[0]["id"])
        selected = next(job for job in scheduler.get_jobs() if job.kwargs["task"] == "inside")
        assert selected.kwargs["project_id"] == projects[0]["id"]
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)
        container._scheduler = None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create_no_workspace", "remember_thread_workspace_ref"])
async def test_workspace_preparation_failure_is_recorded(history_db, monkeypatch, operation):
    import agent.modules.workspaces as workspaces
    from agent.modules.scheduler import service
    from agent.modules.scheduler.history import list_runs

    _patch_execution(monkeypatch)
    monkeypatch.setattr(workspaces, operation, AsyncMock(side_effect=OSError("storage offline")))
    run = AsyncMock()
    monkeypatch.setattr(service, "run_agent_full", run)
    await service.execute_scheduled_task("telegram", "123", "task")
    run.assert_not_awaited()
    record = (await list_runs())["runs"][0]
    assert record["status"] == "failed"
    assert record["error"] == "storage offline"


@pytest.mark.asyncio
async def test_completed_run_is_recorded_with_model(history_db, monkeypatch):
    from agent.modules.scheduler import execute_scheduled_task
    from agent.modules.scheduler.history import list_runs
    from agent.modules.usage import LLMUsageRepository, UsageEventInput

    _patch_execution(monkeypatch, response="all good")

    await execute_scheduled_task("telegram", "123", "daily summary", scheduler_job_id="job-1")

    payload = await list_runs(job_id="job-1")
    assert payload["total"] == 1
    run = payload["runs"][0]
    assert run["status"] == "completed"
    assert run["trigger_source"] == "schedule"
    assert run["response"] == "all good"
    assert run["agent_name"] == "scheduler-executor"
    assert (run["provider"], run["model"]) == ("openai-main", "gpt-test")
    assert run["duration_ms"] is not None
    assert run["models_used"] == []

    await LLMUsageRepository().record(UsageEventInput(
        thread_id=run["thread_id"], root_thread_id=run["thread_id"], platform="bg", user_id="123",
        channel_id="", agent_name="scheduler-executor", provider_name="openai-main",
        model_name="gpt-actual", call_kind="agent", internal=False, has_usage_metadata=True,
        input_tokens=4, output_tokens=6, total_tokens=10, created_at=datetime.now(timezone.utc),
    ))
    run = (await list_runs(job_id="job-1"))["runs"][0]
    assert run["models_used"] == [
        {"provider": "openai-main", "model": "gpt-actual", "calls": 1, "total_tokens": 10}
    ]
    assert run["total_tokens"] == 10


@pytest.mark.asyncio
async def test_failed_run_is_recorded_with_error(history_db, monkeypatch):
    from agent.modules.scheduler import execute_scheduled_task
    from agent.modules.scheduler.history import list_runs

    _patch_execution(monkeypatch, error=RuntimeError("provider down"))

    await execute_scheduled_task(
        "telegram", "123", "daily summary", scheduler_job_id="job-2", trigger_source="manual",
    )

    run = (await list_runs(job_id="job-2"))["runs"][0]
    assert run["status"] == "failed"
    assert run["trigger_source"] == "manual"
    assert run["error"] == "provider down"


@pytest.mark.asyncio
async def test_interrupted_runs_are_closed_and_history_is_pruned(history_db):
    from agent.modules.scheduler.history import (
        finish_run,
        list_runs,
        mark_interrupted_runs,
        prune_runs,
        start_run,
    )

    for index in range(5):
        await start_run(
            run_id=f"run-{index}", job_id="job-3", task="t", platform="telegram", user_id="1",
            trigger_source="schedule", thread_id=f"thread-{index}", agent_name="scheduler-executor",
            provider="p", model="m",
        )
    for index in range(4):
        await finish_run(f"run-{index}", status="completed")

    assert await mark_interrupted_runs() == 1
    statuses = {run["run_id"]: run["status"] for run in (await list_runs(job_id="job-3"))["runs"]}
    assert statuses["run-4"] == "interrupted"

    assert await prune_runs(max_kept=2) == 3
    remaining = [run["run_id"] for run in (await list_runs(job_id="job-3"))["runs"]]
    assert sorted(remaining) == ["run-3", "run-4"]


@pytest_asyncio.fixture
async def scheduler_client(history_db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.delivery.http.dashboard.router import router
    from agent.modules.admin_auth import get_current_admin

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_delete_single_finished_run_preserves_other_runs(scheduler_client, status):
    from agent.modules.scheduler.history import finish_run, start_run

    for run_id, job_id in [("target", "job"), ("same-job", "job"), ("other-job", "other")]:
        await start_run(
            run_id=run_id, job_id=job_id, task="t", platform="api", user_id="1",
            trigger_source="schedule", thread_id=f"thread-{run_id}", agent_name="scheduler-executor",
            provider="p", model="m",
        )
        await finish_run(run_id, status=status)

    response = await scheduler_client.delete("/scheduler/runs/target")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "run_id": "target"}
    history = (await scheduler_client.get("/scheduler/runs")).json()
    assert history["total"] == 2
    assert {run["run_id"] for run in history["runs"]} == {"same-job", "other-job"}
    assert (await scheduler_client.delete("/scheduler/runs/target")).status_code == 404


@pytest.mark.asyncio
async def test_delete_run_rejects_running_and_missing_runs(scheduler_client):
    from agent.modules.scheduler.history import finish_run, start_run

    await start_run(
        run_id="running", job_id="job", task="t", platform="api", user_id="1",
        trigger_source="manual", thread_id="thread-running", agent_name="scheduler-executor",
        provider="p", model="m",
    )
    response = await scheduler_client.delete("/scheduler/runs/running")
    assert response.status_code == 409
    assert "running" in response.json()["detail"]
    assert (await scheduler_client.delete("/scheduler/runs/missing")).status_code == 404
    history = (await scheduler_client.get("/scheduler/runs")).json()
    assert history["total"] == 1
    assert history["runs"][0]["status"] == "running"
    await finish_run("running", status="completed")
    assert (await scheduler_client.delete("/scheduler/runs/running")).status_code == 200


@pytest.mark.asyncio
async def test_delete_single_job_preserves_other_jobs_and_history(scheduler_client):
    import asyncio

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from agent.bootstrap.container import get_active_container
    from agent.modules.scheduler import add_scheduled_task_job
    from agent.modules.scheduler.history import finish_run, start_run

    scheduler = AsyncIOScheduler()
    container = get_active_container()
    container._scheduler = scheduler
    scheduler.start(paused=True)
    try:
        jobs = [add_scheduled_task_job(
            scheduler, platform="api", user_id="1", task=task,
            trigger_type="interval", trigger_args={"minutes": 5},
        ) for task in ("target", "keep")]
        await start_run(
            run_id="past-run", job_id=jobs[0].id, task="target", platform="api", user_id="1",
            trigger_source="schedule", thread_id="thread-past", agent_name="scheduler-executor",
            provider="p", model="m",
        )
        await finish_run("past-run", status="completed")

        response = await scheduler_client.delete(f"/scheduler/jobs/{jobs[0].id}")
        assert response.status_code == 200
        assert scheduler.get_job(jobs[0].id) is None
        assert scheduler.get_job(jobs[1].id) is not None
        history = (await scheduler_client.get("/scheduler/runs", params={"job_id": jobs[0].id})).json()
        assert history["total"] == 1
        assert history["runs"][0]["run_id"] == "past-run"
        assert (await scheduler_client.delete(f"/scheduler/jobs/{jobs[0].id}")).status_code == 404
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)
        container._scheduler = None


@pytest.mark.asyncio
async def test_new_jobs_store_their_own_id_for_history():
    import asyncio

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from agent.modules.scheduler import add_scheduled_task_job
    from agent.modules.scheduler.service import _backfill_job_ids

    scheduler = AsyncIOScheduler()
    scheduler.start(paused=True)
    try:
        job = add_scheduled_task_job(
            scheduler, platform="telegram", user_id="1", task="t",
            trigger_type="interval", trigger_args={"minutes": 5},
        )
        assert job.kwargs["scheduler_job_id"] == job.id

        legacy = scheduler.add_job(
            lambda **_: None, "interval", minutes=5,
            kwargs={"platform": "telegram", "user_id": "1", "task": "old"},
        )
        assert _backfill_job_ids(scheduler) == 1
        assert scheduler.get_job(legacy.id).kwargs["scheduler_job_id"] == legacy.id
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)


def test_describe_executor_model_resolves_default_provider(monkeypatch):
    from types import SimpleNamespace

    import agent.modules.agents as agents_module
    import agent.modules.providers as providers_module
    from agent.modules.scheduler import describe_executor_model

    seen = {}

    def fake_selection(*, provider_name=None, model=None):
        seen["args"] = (provider_name, model)
        return "openai-main", "gpt-test"

    monkeypatch.setattr(
        agents_module,
        "get_catalog_service",
        lambda: SimpleNamespace(get_agent_card=lambda name: SimpleNamespace(provider="default", model="")),
    )
    monkeypatch.setattr(providers_module, "get_chat_model_selection", fake_selection)

    info = describe_executor_model()

    assert seen["args"] == ("default", "")
    assert info == {
        "agent_name": "scheduler-executor",
        "provider": "openai-main",
        "model": "gpt-test",
        "configured": "default",
        "error": "",
    }


def test_describe_executor_model_reports_resolution_error(monkeypatch):
    from types import SimpleNamespace

    import agent.modules.agents as agents_module
    import agent.modules.providers as providers_module
    from agent.modules.scheduler import describe_executor_model

    def failing_selection(**kwargs):
        raise RuntimeError("No enabled providers configured.")

    monkeypatch.setattr(
        agents_module,
        "get_catalog_service",
        lambda: SimpleNamespace(get_agent_card=lambda name: SimpleNamespace(provider="default", model="")),
    )
    monkeypatch.setattr(providers_module, "get_chat_model_selection", failing_selection)

    info = describe_executor_model()

    assert info["model"] == ""
    assert info["error"] == "No enabled providers configured."
