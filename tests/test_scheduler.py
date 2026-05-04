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
