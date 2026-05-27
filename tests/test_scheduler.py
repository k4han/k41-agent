from datetime import datetime, timedelta
import pickle
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Float, LargeBinary, MetaData, String, Table, create_engine, select


def test_schedule_task_in_registry():
    from agent.modules.tools.langchain.registry import get_all_langchain_tools

    tools = get_all_langchain_tools()
    tool_names = [tool.name for tool in tools]
    assert "schedule_task" in tool_names


def test_schedule_task_import():
    from agent.modules.tools.langchain.schedule_tools.schedule import (
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
    monkeypatch.setattr(scheduler_service, "get_workflow_graph", lambda name: FakeGraph())
    monkeypatch.setattr(
        scheduler_service,
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

    run = captured["run"]
    assert run["thread_id"].startswith("bg_telegram_6197833678_daily summary_")
    assert "usage_context" not in run
    assert captured["state_config"] == {
        "configurable": {"thread_id": "telegram_6197833678_6197833678"}
    }
    assert captured["notification"]["args"][:2] == ("telegram", "6197833678")
