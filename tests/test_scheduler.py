from datetime import datetime, timedelta
import pickle
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Float, LargeBinary, MetaData, String, Table, create_engine, select


def test_schedule_task_in_registry():
    from agent.modules.tools import get_default_tools

    tools = get_default_tools()
    tool_names = [tool.name for tool in tools]
    assert "schedule_task" in tool_names


def test_schedule_task_import():
    from agent.modules.tools.builtin.schedule.schedule import (
        ScheduleTaskInput,
        schedule_task,
    )

    assert schedule_task is not None
    assert ScheduleTaskInput is not None

    schema = ScheduleTaskInput.model_json_schema()
    required_fields = schema.get("required", [])
    assert "task_description" in required_fields
    assert "relative" in schema["properties"]["trigger_type"]["enum"]


class FakeScheduler:
    timezone = ZoneInfo("Asia/Bangkok")


def test_relative_trigger_converts_to_one_time_date():
    from agent.modules.scheduler import normalize_trigger

    now = datetime(2026, 4, 13, 22, 30, 0, tzinfo=FakeScheduler.timezone)

    trigger_type, trigger_args = normalize_trigger(
        "relative",
        {"minutes": 2},
        FakeScheduler(),
        now=now,
    )

    assert trigger_type == "date"
    assert trigger_args["run_date"] == now + timedelta(minutes=2)


def test_date_trigger_rejects_past_run_date():
    from agent.modules.scheduler import normalize_trigger

    now = datetime(2026, 4, 13, 22, 30, 0, tzinfo=FakeScheduler.timezone)

    with pytest.raises(ValueError, match="is in the past"):
        normalize_trigger(
            "date",
            {"run_date": "2026-04-13 14:30:00"},
            FakeScheduler(),
            now=now,
        )


def test_interval_trigger_rejects_empty_duration():
    from agent.modules.scheduler import normalize_trigger

    now = datetime(2026, 4, 13, 22, 30, 0, tzinfo=FakeScheduler.timezone)

    with pytest.raises(ValueError, match="at least one positive value"):
        normalize_trigger(
            "interval",
            {},
            FakeScheduler(),
            now=now,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["tool", "dashboard"])
@pytest.mark.parametrize("trigger_type", ["cron", "date"])
async def test_new_jobs_use_updated_timezone_without_restart(
    isolated_container, entrypoint, trigger_type
):
    import asyncio
    from types import SimpleNamespace

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from agent.delivery.http.dashboard.routes.scheduler import (
        CreateJobBody,
        UpdateJobBody,
        create_scheduler_job,
        update_scheduler_job,
    )
    from agent.delivery.http.dashboard.routes.settings import UpdateSettingsBody, update_settings
    from agent.modules.tools.builtin.schedule.schedule import schedule_task
    from agent.shared.config import ConfigService
    from agent.shared.config.database_source import DatabaseConfigSource
    from agent.shared.config.default_source import DefaultConfigSource

    source = DatabaseConfigSource(isolated_container.database_url)
    service = ConfigService([DefaultConfigSource(), source])
    isolated_container.config_service = service
    service.update_setting("display.timezone", "UTC")
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("UTC"))
    isolated_container._scheduler = scheduler
    scheduler.start(paused=True)

    try:
        old_job = scheduler.add_job(lambda: None, "cron", hour=6, minute=0)
        old_next_run = old_job.next_run_time
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(config_service=service))
        )
        await update_settings(
            UpdateSettingsBody(values={"display.timezone": "Asia/Ho_Chi_Minh"}), request
        )
        trigger_args = (
            {"hour": "6", "minute": "0"}
            if trigger_type == "cron"
            else {"run_date": "2099-11-08 06:00:00"}
        )

        if entrypoint == "tool":
            runtime = SimpleNamespace(config={
                "configurable": {"thread_id": "new-conversation"},
                "metadata": {"usage_context": {"platform": "telegram", "user_id": "123"}},
            })
            result = schedule_task.func("Remind me to eat.", trigger_type, trigger_args, runtime)
            assert "Successfully scheduled" in result
            assert "UTC" not in result
            job = next(job for job in scheduler.get_jobs() if job.id != old_job.id)
        else:
            result = await create_scheduler_job(CreateJobBody(
                task="Remind me to eat.", platform="telegram", user_id="123",
                trigger_type=trigger_type, trigger_args=trigger_args,
            ))
            job = scheduler.get_job(result["job"]["id"])

        zone = ZoneInfo("Asia/Ho_Chi_Minh")
        anchor = datetime(2099, 11, 8, tzinfo=zone)
        expected = datetime(2099, 11, 8, 6, tzinfo=zone)
        assert job.trigger.get_next_fire_time(None, anchor) == expected
        assert job.next_run_time.utcoffset() == timedelta(hours=7)
        assert str(old_job.trigger.timezone) == "UTC"
        assert old_job.next_run_time == old_next_run

        if entrypoint == "dashboard":
            await update_scheduler_job(old_job.id, UpdateJobBody(
                trigger_type="cron", trigger_args={"hour": "6", "minute": "0"},
            ))
            assert old_job.trigger.get_next_fire_time(None, anchor) == expected
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)
        isolated_container._scheduler = None
        source.close()


def test_migrates_legacy_scheduler_job_references():
    from agent.modules.scheduler.service import (
        CURRENT_EXECUTE_TASK_REF,
        LEGACY_EXECUTE_TASK_REF,
        _migrate_legacy_job_references,
    )

    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    jobs = Table(
        "apscheduler_jobs",
        metadata,
        Column("id", String(191), primary_key=True),
        Column("next_run_time", Float),
        Column("job_state", LargeBinary, nullable=False),
    )
    metadata.create_all(engine)

    legacy_state = {"func": LEGACY_EXECUTE_TASK_REF, "other": "kept"}
    current_state = {"func": CURRENT_EXECUTE_TASK_REF}
    with engine.begin() as conn:
        conn.execute(
            jobs.insert(),
            [
                {
                    "id": "legacy",
                    "next_run_time": None,
                    "job_state": pickle.dumps(legacy_state),
                },
                {
                    "id": "current",
                    "next_run_time": None,
                    "job_state": pickle.dumps(current_state),
                },
            ],
        )

    assert _migrate_legacy_job_references(engine) == 1

    with engine.connect() as conn:
        rows = {
            row.id: pickle.loads(row.job_state)
            for row in conn.execute(select(jobs.c.id, jobs.c.job_state))
        }

    assert rows["legacy"]["func"] == CURRENT_EXECUTE_TASK_REF
    assert rows["legacy"]["other"] == "kept"
    assert rows["current"]["func"] == CURRENT_EXECUTE_TASK_REF


def test_scheduler_job_reference_migration_skips_missing_table():
    from agent.modules.scheduler.service import _migrate_legacy_job_references

    engine = create_engine("sqlite:///:memory:")

    assert _migrate_legacy_job_references(engine) == 0


@pytest.mark.asyncio
async def test_execute_scheduled_task_uses_scheduled_thread_for_usage(monkeypatch):
    import agent.modules.conversations as conversations_module
    import agent.modules.workflows as workflows_module
    from agent.modules.scheduler import service as scheduler_service

    captured: dict = {}

    class FakeGraph:
        async def aupdate_state(self, config, values, *, as_node):
            captured["state_config"] = config
            captured["state_values"] = values
            captured["state_node"] = as_node

    async def fake_run_agent_full(**kwargs):
        captured["run"] = kwargs
        return "done"

    async def fake_upsert_conversation_thread(**kwargs):
        captured["upsert"] = kwargs

    async def fake_send_notification(*args, **kwargs):
        captured["notification"] = {"args": args, "kwargs": kwargs}
        return True

    monkeypatch.setattr(scheduler_service, "run_agent_full", fake_run_agent_full)
    monkeypatch.setattr(workflows_module, "get_workflow_graph", lambda name: FakeGraph())
    monkeypatch.setattr(
        workflows_module,
        "make_run_config",
        lambda *, thread_id: {"configurable": {"thread_id": thread_id}},
    )
    monkeypatch.setattr(scheduler_service, "_send_notification", fake_send_notification)
    monkeypatch.setattr(
        conversations_module,
        "upsert_conversation_thread",
        fake_upsert_conversation_thread,
    )

    await scheduler_service.execute_scheduled_task(
        "telegram",
        "6197833678",
        "daily summary",
    )

    from agent.modules.agent_runtime.session import SessionManager

    assert "agent_name" not in captured["upsert"]
    run = captured["run"]
    assert len(run["thread_id"]) == 32
    assert run["usage_context"]["platform"] == "bg"
    assert run["usage_context"]["user_id"] == "6197833678"
    assert captured["state_config"] == {
        "configurable": {"thread_id": SessionManager.make_thread_id("telegram", "6197833678", "6197833678")}
    }
    assert captured["state_node"] == "llm"
    messages = captured["state_values"]["messages"]
    assert messages[0].content == "[Scheduled Task]\ndaily summary"
    assert messages[1].content == "done"
    assert captured["notification"]["args"][:2] == ("telegram", "6197833678")
