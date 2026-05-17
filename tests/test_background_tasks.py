import asyncio
import time

import pytest
import pytest_asyncio

from agent.modules.agent_runtime.background_tasks import (
    BackgroundTask,
    BackgroundTaskManager,
    MAX_COMPLETED_TASKS,
    NotifyChannel,
    TaskStatus,
)
from agent.modules.agent_runtime.repository import BackgroundTaskRepository
from agent.shared.infrastructure.db import Base, load_orm_models
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine


@pytest_asyncio.fixture
async def background_task_db(monkeypatch: pytest.MonkeyPatch, tmp_path, request):
    await close_async_engine()

    db_path = tmp_path / f"{request.node.name}.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("PERSISTENCE_ALLOW_ANY_PATH", "true")

    import agent.shared.infrastructure.db.engine as engine_module

    monkeypatch.setattr(engine_module, "get_database_url", lambda: db_url)
    engine_module._cached_database_url = None

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)

    try:
        yield
    finally:
        await close_async_engine()


async def _wait_for_task_status(
    manager: BackgroundTaskManager,
    task_id: str,
    status: str,
) -> dict:
    for _ in range(100):
        task = manager.get(task_id)
        runtime_task = manager._tasks.get(task_id)
        if task and task["status"] == status and runtime_task and runtime_task._async_task is None:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not reach status {status}.")


@pytest.mark.asyncio
async def test_background_task_restore_preserves_completed_status(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module

    async def fake_run_agent_full(**kwargs):
        return "done"

    monkeypatch.setattr(runner_module, "run_agent_full", fake_run_agent_full)

    manager = BackgroundTaskManager()
    task_id = await manager.submit("do work", agent_name="default")
    task = await _wait_for_task_status(manager, task_id, "completed")

    restored = BackgroundTaskManager()
    await restored.restore_from_persistence()
    restored_task = restored.get(task_id)

    assert task["result"] == "done"
    assert restored_task is not None
    assert restored_task["status"] == "completed"
    assert restored_task["result"] == "done"
    assert restored_task["error"] == ""


@pytest.mark.asyncio
async def test_background_task_restore_marks_running_records_interrupted(
    background_task_db,
) -> None:
    repository = BackgroundTaskRepository()
    now = time.time()
    await repository.upsert(
        task_id="running-task",
        thread_id="task_dashboard_running-task",
        request="do work",
        agent_name="default",
        working_dir=None,
        notify_platform="",
        notify_external_id="",
        notify_channel_id="",
        status="running",
        result="",
        error="",
        created_at=now - 20,
        started_at=now - 10,
        completed_at=None,
    )

    manager = BackgroundTaskManager()
    await manager.restore_from_persistence()
    task = manager.get("running-task")
    records = await repository.list()

    assert task is not None
    assert task["status"] == "failed"
    assert task["error"] == "Interrupted by server restart"
    assert records[0]["status"] == "failed"
    assert records[0]["error"] == "Interrupted by server restart"


@pytest.mark.asyncio
async def test_background_task_remove_is_persisted(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module

    async def fake_run_agent_full(**kwargs):
        return "done"

    monkeypatch.setattr(runner_module, "run_agent_full", fake_run_agent_full)

    manager = BackgroundTaskManager()
    task_id = await manager.submit("do work", agent_name="default")
    await _wait_for_task_status(manager, task_id, "completed")

    assert await manager.remove(task_id) is True

    restored = BackgroundTaskManager()
    await restored.restore_from_persistence()

    assert restored.get(task_id) is None
    assert restored.list_all() == []


@pytest.mark.asyncio
async def test_background_task_restore_keeps_memory_bounded(
    background_task_db,
) -> None:
    repository = BackgroundTaskRepository()
    start = time.time() - 1000
    for index in range(MAX_COMPLETED_TASKS + 5):
        await repository.upsert(
            task_id=f"task-{index}",
            thread_id=f"task_dashboard_task-{index}",
            request=f"work {index}",
            agent_name="default",
            working_dir=None,
            notify_platform="",
            notify_external_id="",
            notify_channel_id="",
            status="completed",
            result="done",
            error="",
            created_at=start + index,
            started_at=start + index,
            completed_at=start + index + 1,
        )

    manager = BackgroundTaskManager()
    await manager.restore_from_persistence()

    assert len(manager.list_all()) == MAX_COMPLETED_TASKS


@pytest.mark.asyncio
async def test_background_task_completion_notification_preserves_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.background_tasks as background_tasks_module

    sent: dict = {}

    async def fake_send_notification(**kwargs):
        sent.update(kwargs)
        return True

    async def fake_inject_into_user_thread(task: BackgroundTask) -> None:
        return None

    monkeypatch.setattr(
        background_tasks_module,
        "_send_notification",
        fake_send_notification,
    )

    manager = BackgroundTaskManager()
    monkeypatch.setattr(
        manager,
        "_inject_into_user_thread",
        fake_inject_into_user_thread,
    )

    task = BackgroundTask(
        request="summarize",
        notify_channel=NotifyChannel(platform="telegram", external_id="123"),
        status=TaskStatus.COMPLETED,
        result="**bold** and `code`",
    )

    await manager._notify_completion(task)

    assert sent["platform"] == "telegram"
    assert sent["external_id"] == "123"
    assert sent["mode"] == "markdown"
    assert "**bold** and `code`" in sent["message"]
