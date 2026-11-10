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


@pytest.mark.parametrize(
    ("day_of_week", "expected"),
    [
        ("1-5", "mon-fri"),
        ("1", "mon"),
        ("0", "sun"),
        ("7", "sun"),
        ("0,6", "sat,sun"),
        (1, "mon"),
        ("MON-FRI", "mon-fri"),
        ("*", "*"),
    ],
)
def test_cron_trigger_reads_numeric_weekdays_as_crontab(day_of_week, expected):
    from agent.modules.scheduler import normalize_trigger

    _, trigger_args = normalize_trigger(
        "cron",
        {"minute": "0", "hour": "9", "day_of_week": day_of_week},
        FakeScheduler(),
    )

    assert trigger_args["day_of_week"] == expected


def test_cron_trigger_rejects_invalid_weekday_number():
    from agent.modules.scheduler import normalize_trigger

    with pytest.raises(ValueError, match="Invalid day_of_week"):
        normalize_trigger("cron", {"day_of_week": "8"}, FakeScheduler())


def test_serialize_trigger_args_reads_real_trigger_fields():
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from agent.modules.scheduler import describe_trigger, serialize_trigger_args

    zone = FakeScheduler.timezone
    cron = CronTrigger(minute="30", hour="7", day_of_week="mon-fri", timezone=zone)
    assert serialize_trigger_args(cron) == {
        "minute": "30", "hour": "7", "day": "*", "month": "*", "day_of_week": "mon-fri",
    }
    assert describe_trigger(cron) == "cron 30 7 * * mon-fri"

    # Legacy jobs stored APScheduler numbering (0 = Monday); show what actually runs.
    legacy = CronTrigger(minute="0", hour="9", day_of_week="1-5", timezone=zone)
    assert serialize_trigger_args(legacy)["day_of_week"] == "tue-sat"

    interval = IntervalTrigger(days=8, hours=1, minutes=30, timezone=zone)
    assert serialize_trigger_args(interval) == {"weeks": 1, "days": 1, "hours": 1, "minutes": 30}
    assert describe_trigger(interval) == "every 1 weeks 1 days 1 hours 30 minutes"


@pytest.mark.parametrize("seconds", [0.5, 1.5, 60.000001, 691201.234567])
def test_fractional_interval_survives_dashboard_round_trip(seconds):
    from types import SimpleNamespace

    from apscheduler.triggers.interval import IntervalTrigger

    from agent.delivery.http.dashboard.routes.helpers.scheduler import serialize_job
    from agent.modules.scheduler import describe_trigger, normalize_trigger

    trigger = IntervalTrigger(seconds=seconds, timezone=FakeScheduler.timezone)
    job = SimpleNamespace(
        id="job-1",
        kwargs={"task": "t", "platform": "api", "user_id": "u"},
        trigger=trigger,
        next_run_time=None,
    )

    payload = serialize_job(job)
    trigger_type, trigger_args = normalize_trigger(
        payload["trigger_type"], payload["trigger_args"], FakeScheduler()
    )
    restored = IntervalTrigger(timezone=FakeScheduler.timezone, **trigger_args)

    assert trigger_type == "interval"
    assert restored.interval == trigger.interval
    assert payload["trigger_args"]["seconds"] % 1 == pytest.approx(seconds % 1)
    assert f"{payload['trigger_args']['seconds']} seconds" in describe_trigger(trigger)


@pytest.mark.parametrize(
    ("trigger_args", "expected"),
    [
        ({"week": "1", "hour": "9"}, "cron 0 9 * * * (week=1)"),
        ({"year": "2030", "hour": "9"}, "cron 0 9 * * * (year=2030)"),
        ({"second": "*/10"}, "cron * * * * * (second=*/10)"),
        ({}, "cron * * * * * (second=*)"),
        ({"minute": "*", "second": "0"}, "cron * * * * *"),
        (
            {"year": "2030", "week": "1", "hour": "9", "second": "30"},
            "cron * 9 * * * (year=2030, week=1, second=30)",
        ),
    ],
)
def test_describe_cron_includes_additional_fields(trigger_args, expected):
    from apscheduler.triggers.cron import CronTrigger

    from agent.modules.scheduler import describe_trigger

    trigger = CronTrigger(timezone=FakeScheduler.timezone, **trigger_args)

    assert describe_trigger(trigger) == expected


def test_describe_cron_includes_bounds_and_jitter():
    from apscheduler.triggers.cron import CronTrigger

    from agent.modules.scheduler import describe_trigger

    trigger = CronTrigger(
        hour=9,
        start_date="2030-01-01",
        end_date="2030-01-31",
        jitter=5,
        timezone=FakeScheduler.timezone,
    )

    assert describe_trigger(trigger) == (
        "cron 0 9 * * * (start_date=2030-01-01T00:00:00+07:00, "
        "end_date=2030-01-31T00:00:00+07:00, jitter=5)"
    )


def test_serialize_job_returns_cron_fields_for_dashboard():
    from types import SimpleNamespace

    from apscheduler.triggers.cron import CronTrigger

    from agent.delivery.http.dashboard.routes.helpers.scheduler import serialize_job
    from agent.modules.scheduler import normalize_trigger

    trigger_type, trigger_args = normalize_trigger(
        "cron", {"minute": "0", "hour": "9", "day_of_week": "1-5"}, FakeScheduler()
    )
    job = SimpleNamespace(
        id="job-1",
        kwargs={"task": "t", "platform": "api", "user_id": "u"},
        trigger=CronTrigger(timezone=FakeScheduler.timezone, **trigger_args),
        next_run_time=None,
    )

    payload = serialize_job(job)

    assert trigger_type == "cron"
    assert payload["trigger_type"] == "cron"
    assert payload["trigger_args"] == {
        "minute": "0", "hour": "9", "day": "*", "month": "*", "day_of_week": "mon-fri",
    }


def test_task_title_keeps_first_line_short():
    from agent.modules.scheduler.service import TASK_TITLE_MAX_LEN, _task_title

    assert _task_title("\n  Remind me to drink water\nmore details") == "Remind me to drink water"
    long_title = _task_title("x" * 200)
    assert len(long_title) == TASK_TITLE_MAX_LEN
    assert long_title.endswith("…")


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
            result = await schedule_task.coroutine("Remind me to eat.", trigger_type, trigger_args, runtime)
            assert "Successfully scheduled" in result
            assert "UTC" not in result
            assert "Remind me to eat." not in result
            job = next(job for job in scheduler.get_jobs() if job.id != old_job.id)
        else:
            result = await create_scheduler_job(CreateJobBody(
                task="Remind me to eat.", platform="telegram", user_id="123",
                trigger_type=trigger_type, trigger_args=trigger_args,
            ), admin="1")
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
            ), admin="1")
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
async def test_execute_scheduled_task_uses_scheduled_thread_for_usage(monkeypatch, tmp_path):
    import agent.modules.conversations as conversations_module
    import agent.modules.workspaces as workspaces_module
    from agent.modules.scheduler import service as scheduler_service
    from unittest.mock import AsyncMock

    captured: dict = {"upserts": []}
    workspace = workspaces_module.WorkspaceRef(locator=str(tmp_path), metadata={"no_workspace": True})

    async def fake_run_agent_full(**kwargs):
        captured["run"] = kwargs
        return "done"

    async def fake_upsert_conversation_thread(**kwargs):
        captured["upserts"].append(kwargs)

    async def fake_send_notification(*args, **kwargs):
        captured["notification"] = {"args": args, "kwargs": kwargs}
        return True

    monkeypatch.setattr(scheduler_service, "run_agent_full", fake_run_agent_full)
    monkeypatch.setattr(scheduler_service, "_record_run_start", AsyncMock(return_value=False))
    create = AsyncMock(return_value=workspace)
    remember = AsyncMock()
    monkeypatch.setattr(workspaces_module, "create_no_workspace", create)
    monkeypatch.setattr(workspaces_module, "remember_thread_workspace_ref", remember)
    inject = AsyncMock(side_effect=AssertionError("Scheduler results must stay in their own session."))
    monkeypatch.setattr(conversations_module, "inject_agent_message_pair", inject)
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
    assert len(captured["upserts"]) == 1
    assert captured["upserts"][0]["kind"] == "scheduled"
    assert captured["upserts"][0]["thread_id"] == run["thread_id"]
    assert run["workspace"] == workspace
    create.assert_awaited_once_with(run["thread_id"])
    remember.assert_awaited_once_with(run["thread_id"], workspace)
    inject.assert_not_awaited()
    assert len(run["thread_id"]) == 32
    assert run["usage_context"]["platform"] == "bg"
    assert run["usage_context"]["user_id"] == "6197833678"
    assert captured["notification"]["args"][:2] == ("telegram", "6197833678")
    assert captured["notification"]["args"][2] == "**Scheduled task:** daily summary\n\ndone"


async def _run_scheduled_task(monkeypatch, tmp_path, platform: str, **task_kwargs) -> dict:
    import agent.modules.conversations as conversations_module
    import agent.modules.workspaces as workspaces_module
    from agent.modules.scheduler import service as scheduler_service
    from unittest.mock import AsyncMock

    captured: dict = {}
    workspace = workspaces_module.WorkspaceRef(locator=str(tmp_path), metadata={"no_workspace": True})

    async def fake_run_agent_full(**kwargs):
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(scheduler_service, "run_agent_full", fake_run_agent_full)
    monkeypatch.setattr(scheduler_service, "_record_run_start", AsyncMock(return_value=False))
    monkeypatch.setattr(scheduler_service, "_send_notification", AsyncMock(return_value=True))
    monkeypatch.setattr(workspaces_module, "create_no_workspace", AsyncMock(return_value=workspace))
    monkeypatch.setattr(workspaces_module, "remember_thread_workspace_ref", AsyncMock())
    monkeypatch.setattr(conversations_module, "upsert_conversation_thread", AsyncMock())

    await scheduler_service.execute_scheduled_task(platform, "123", "open a page", **task_kwargs)
    return captured


@pytest.mark.asyncio
async def test_scheduled_run_uses_the_stored_browser_owner(monkeypatch, tmp_path):
    run = await _run_scheduled_task(monkeypatch, tmp_path, "telegram", browser_owner_id="admin:7")
    assert run["browser_owner_id"] == "admin:7"


@pytest.mark.asyncio
async def test_scheduled_run_without_browser_keeps_service_uninitialized(isolated_container, monkeypatch, tmp_path):
    assert isolated_container._browser_service is None
    await _run_scheduled_task(monkeypatch, tmp_path, "telegram")
    assert isolated_container._browser_service is None


@pytest.mark.asyncio
async def test_browser_cleanup_failure_does_not_replace_scheduled_result(isolated_container, monkeypatch, tmp_path, caplog):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agent.modules.scheduler import service as scheduler

    release = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    isolated_container._browser_service = SimpleNamespace(release_thread=release)
    run = await _run_scheduled_task(monkeypatch, tmp_path, "telegram")
    release.assert_awaited_once_with(run["thread_id"])
    scheduler._send_notification.assert_awaited_once_with(
        "telegram", "123", "**Scheduled task:** open a page\n\ndone", mode="markdown")
    assert "Browser cleanup failed for finished thread" in caplog.text


@pytest.mark.asyncio
async def test_legacy_dashboard_job_falls_back_to_the_active_admin(monkeypatch, tmp_path):
    import agent.modules.admin_auth as admin_auth_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    service = SimpleNamespace(get_admin=AsyncMock(return_value=SimpleNamespace(id=3)))
    monkeypatch.setattr(admin_auth_module, "get_admin_auth_service", lambda: service)

    assert (await _run_scheduled_task(monkeypatch, tmp_path, "api"))["browser_owner_id"] == "admin:3"
    assert (await _run_scheduled_task(monkeypatch, tmp_path, "telegram"))["browser_owner_id"] is None


@pytest.mark.asyncio
async def test_schedule_task_tool_stores_the_conversation_browser_owner(monkeypatch):
    from types import SimpleNamespace
    from agent.modules.tools.builtin.schedule import schedule as schedule_module

    captured: dict = {}

    def fake_add_job(scheduler, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="job", trigger=None, next_run_time=None)

    monkeypatch.setattr(schedule_module, "add_scheduled_task_job", fake_add_job)
    monkeypatch.setattr(schedule_module, "get_scheduler", lambda: None)
    monkeypatch.setattr(schedule_module, "normalize_trigger", lambda kind, args, _: (kind, args))
    monkeypatch.setattr(schedule_module, "describe_trigger", lambda trigger: "once")

    async def no_project(project_id):
        return None

    monkeypatch.setattr(schedule_module, "resolve_scheduled_project", no_project)

    for context, expected in (({"browser_owner_id": "admin:1"}, "admin:1"), ({}, None)):
        runtime = SimpleNamespace(context=context, config={
            "configurable": {"thread_id": "conversation"},
            "metadata": {"usage_context": {"platform": "api", "user_id": "dashboard"}},
        })
        await schedule_module.schedule_task.coroutine("Open a page.", "date", {"run_date": "x"}, runtime)
        assert captured["browser_owner_id"] == expected
