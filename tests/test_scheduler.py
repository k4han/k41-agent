from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest


def test_schedule_task_in_registry():
    from agent.modules.tools.infrastructure.langchain.registry import get_all_langchain_tools

    tools = get_all_langchain_tools()
    tool_names = [tool.name for tool in tools]
    assert "schedule_task" in tool_names


def test_schedule_task_import():
    from agent.modules.tools.infrastructure.langchain.schedule_tools.schedule import (
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
    from agent.modules.scheduler.public import normalize_trigger

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
    from agent.modules.scheduler.public import normalize_trigger

    now = datetime(2026, 4, 13, 22, 30, 0, tzinfo=FakeScheduler.timezone)

    with pytest.raises(ValueError, match="is in the past"):
        normalize_trigger(
            "date",
            {"run_date": "2026-04-13 14:30:00"},
            FakeScheduler(),
            now=now,
        )


def test_interval_trigger_rejects_empty_duration():
    from agent.modules.scheduler.public import normalize_trigger

    now = datetime(2026, 4, 13, 22, 30, 0, tzinfo=FakeScheduler.timezone)

    with pytest.raises(ValueError, match="at least one positive value"):
        normalize_trigger(
            "interval",
            {},
            FakeScheduler(),
            now=now,
        )
