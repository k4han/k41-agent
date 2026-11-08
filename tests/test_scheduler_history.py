from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from agent.shared.infrastructure.db import Base, load_orm_models
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine


@pytest_asyncio.fixture
async def history_db(tmp_path, request):
    await close_async_engine()

    db_path = tmp_path / f"{request.node.name}.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"

    from agent.bootstrap.container import create_test_container, set_active_container

    set_active_container(create_test_container(database_url=db_url))

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)

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
    monkeypatch.setattr(conversations_module, "upsert_conversation_thread", fake_noop)
    monkeypatch.setattr(conversations_module, "inject_agent_message_pair", fake_noop)


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
