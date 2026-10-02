import asyncio
import importlib
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

    from agent.bootstrap.container import create_test_container, set_active_container

    set_active_container(create_test_container(database_url=db_url))

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


async def _wait_for_thread_title(thread_id: str, title: str) -> dict:
    from agent.modules.conversations import get_conversation_thread

    for _ in range(100):
        thread = await get_conversation_thread(thread_id)
        if thread and thread["title"] == title:
            return thread
        await asyncio.sleep(0.01)
    raise AssertionError(f"Thread {thread_id} did not reach title {title!r}.")


@pytest.mark.asyncio
async def test_background_task_restore_preserves_completed_status(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module

    async def fake_run_agent_stream(**kwargs):
        yield {"type": "final", "content": "done"}

    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)

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
async def test_background_task_submit_remembers_thread_workspace(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module
    from agent.modules.workspaces import get_thread_workspace_ref, workspace_ref_from_local_path

    async def fake_run_agent_stream(**kwargs):
        yield {"type": "final", "content": "done"}

    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)

    workspace = workspace_ref_from_local_path(
        str(tmp_path),
        label="octo/example",
        metadata={"source": "github"},
    )
    manager = BackgroundTaskManager()
    task_id = await manager.submit(
        "do work",
        agent_name="default",
        workspace=workspace,
    )
    task = await _wait_for_task_status(manager, task_id, "completed")
    stored_workspace = await get_thread_workspace_ref(task["thread_id"])

    assert stored_workspace is not None
    assert stored_workspace.model_dump() == workspace.model_dump()


@pytest.mark.asyncio
async def test_background_task_usage_context_uses_task_thread_when_notify_channel_set(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.background_tasks as background_tasks_module
    import agent.modules.agent_runtime.runner as runner_module

    captured: dict = {}

    async def fake_run_agent_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "final", "content": "done"}

    async def fake_send_notification(**kwargs):
        return True

    async def fake_inject_into_user_thread(task: BackgroundTask) -> None:
        return None

    monkeypatch.setattr(
        background_tasks_module,
        "_send_notification",
        fake_send_notification,
    )
    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)

    manager = BackgroundTaskManager()
    monkeypatch.setattr(
        manager,
        "_inject_into_user_thread",
        fake_inject_into_user_thread,
    )
    task_id = await manager.submit(
        "do work",
        agent_name="default",
        notify_channel=NotifyChannel(
            platform="telegram",
            external_id="6197833678",
            channel_id="6197833678",
        ),
    )
    task = await _wait_for_task_status(manager, task_id, "completed")

    assert task["thread_id"] == f"task_dashboard_{task_id}"
    assert captured["usage_context"] == {
        "platform": "task",
        "user_id": "dashboard",
        "channel_id": task_id,
    }


@pytest.mark.asyncio
async def test_background_task_submit_generates_conversation_title(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module
    from agent.modules.conversations import get_conversation_thread
    from agent.modules.conversations import service as conversation_service

    calls: dict = {}
    generation_started = asyncio.Event()
    release_generation = asyncio.Event()

    async def fake_run_agent_stream(**kwargs):
        yield {"type": "final", "content": "done"}

    async def fake_generate_conversation_title(**kwargs):
        calls["generate"] = kwargs
        generation_started.set()
        await release_generation.wait()
        return "Generated Background Title"

    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)
    monkeypatch.setattr(
        conversation_service,
        "generate_conversation_title",
        fake_generate_conversation_title,
    )

    request = "Inspect the failing dashboard background task naming flow"
    manager = BackgroundTaskManager()
    task_id = await manager.submit(request, agent_name="default")
    task = manager.get(task_id)
    assert task is not None

    await asyncio.wait_for(generation_started.wait(), timeout=1)
    thread = await get_conversation_thread(task["thread_id"])
    assert thread is not None
    assert thread["title"] == request
    assert calls["generate"] == {
        "first_user_message": request,
        "attachments": None,
        "thread_id": task["thread_id"],
    }

    release_generation.set()
    await _wait_for_thread_title(task["thread_id"], "Generated Background Title")
    await _wait_for_task_status(manager, task_id, "completed")


@pytest.mark.asyncio
async def test_background_task_generated_title_does_not_overwrite_manual_rename(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module
    from agent.modules.conversations import (
        get_conversation_thread,
        rename_conversation_thread,
    )
    from agent.modules.conversations import service as conversation_service

    generation_started = asyncio.Event()
    release_generation = asyncio.Event()
    update_attempted = asyncio.Event()
    original_update_title = (
        conversation_service.update_conversation_thread_title_if_current
    )

    async def fake_run_agent_stream(**kwargs):
        yield {"type": "final", "content": "done"}

    async def fake_generate_conversation_title(**kwargs):
        generation_started.set()
        await release_generation.wait()
        return "Generated Background Title"

    async def wrapped_update_conversation_thread_title_if_current(**kwargs):
        try:
            return await original_update_title(**kwargs)
        finally:
            update_attempted.set()

    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)
    monkeypatch.setattr(
        conversation_service,
        "generate_conversation_title",
        fake_generate_conversation_title,
    )
    monkeypatch.setattr(
        conversation_service,
        "update_conversation_thread_title_if_current",
        wrapped_update_conversation_thread_title_if_current,
    )

    manager = BackgroundTaskManager()
    task_id = await manager.submit("Summarize the current workspace", agent_name="default")
    task = manager.get(task_id)
    assert task is not None

    await asyncio.wait_for(generation_started.wait(), timeout=1)
    await rename_conversation_thread(task["thread_id"], "Manual Background Title")
    release_generation.set()
    await asyncio.wait_for(update_attempted.wait(), timeout=1)
    await _wait_for_task_status(manager, task_id, "completed")

    thread = await get_conversation_thread(task["thread_id"])
    assert thread is not None
    assert thread["title"] == "Manual Background Title"


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
    import agent.modules.workspaces as workspaces_module
    import agent.modules.workflows as workflows_module

    async def fake_run_agent_stream(**kwargs):
        yield {"type": "final", "content": "done"}

    deleted_thread_ids: list[str] = []
    deleted_workspace_thread_ids: list[str] = []
    closed_thread_ids: list[str] = []

    shell_manager_module = importlib.import_module(
        "agent.modules.tools.builtin.shell.session_manager"
    )

    class FakeSessionManager:
        def close_thread_sessions(self, thread_id: str) -> int:
            closed_thread_ids.append(thread_id)
            return 1

    async def fake_delete_workflow_thread_tree(thread_id: str) -> None:
        deleted_thread_ids.append(thread_id)

    async def fake_delete_thread_workspace(thread_id: str) -> None:
        deleted_workspace_thread_ids.append(thread_id)

    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)
    monkeypatch.setattr(shell_manager_module, "session_manager", FakeSessionManager())
    monkeypatch.setattr(
        workspaces_module,
        "delete_thread_workspace",
        fake_delete_thread_workspace,
    )
    monkeypatch.setattr(
        workflows_module,
        "delete_workflow_thread_tree",
        fake_delete_workflow_thread_tree,
    )

    manager = BackgroundTaskManager()
    task_id = await manager.submit("do work", agent_name="default")
    task = await _wait_for_task_status(manager, task_id, "completed")

    assert await manager.remove(task_id) is True

    restored = BackgroundTaskManager()
    await restored.restore_from_persistence()

    assert restored.get(task_id) is None
    assert restored.list_all() == []
    assert closed_thread_ids == [task["thread_id"]]
    assert deleted_workspace_thread_ids == [task["thread_id"]]
    assert deleted_thread_ids == [task["thread_id"]]


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


@pytest.mark.asyncio
async def test_background_task_injects_telegram_state_without_conversation_or_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import agent.modules.conversations as conversations_module
    import agent.modules.workflows as workflows_module
    import agent.modules.workspaces as workspaces_module
    from agent.modules.workspaces import workspace_ref_from_local_path

    captured: dict = {}

    class FakeGraph:
        async def aupdate_state(self, config, values, *, as_node):
            captured["config"] = config
            captured["values"] = values
            captured["as_node"] = as_node

    async def fail_upsert_conversation_thread(**kwargs):
        raise AssertionError("notification injection must not create a conversation")

    async def fail_remember_thread_workspace_ref(thread_id, workspace):
        raise AssertionError("notification injection must not change Telegram workspace")

    monkeypatch.setattr(workflows_module, "get_workflow_graph", lambda name: FakeGraph())
    monkeypatch.setattr(
        workflows_module,
        "make_run_config",
        lambda *, thread_id: {"thread_id": thread_id},
    )
    monkeypatch.setattr(
        conversations_module,
        "upsert_conversation_thread",
        fail_upsert_conversation_thread,
    )
    monkeypatch.setattr(
        workspaces_module,
        "remember_thread_workspace_ref",
        fail_remember_thread_workspace_ref,
    )

    workspace = workspace_ref_from_local_path(
        str(tmp_path),
        label="octo/example",
        metadata={"source": "github"},
    )
    task = BackgroundTask(
        request="fix issue",
        agent_name="default",
        workspace=workspace,
        notify_channel=NotifyChannel(
            platform="telegram",
            external_id="123",
            channel_id="456",
        ),
        result="done",
    )

    await BackgroundTaskManager()._inject_into_user_thread(task)

    assert captured["config"] == {"thread_id": "telegram_123_456"}
    assert captured["as_node"] == "llm"
    messages = captured["values"]["messages"]
    assert messages[0].content == "[Background Task]\nfix issue"
    assert messages[1].content == "done"


@pytest.mark.asyncio
async def test_background_task_session_stays_active_through_completion_hook(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dashboard session must cover the whole task lifecycle.

    Regression test: previously the active session was unregistered as soon as
    the agent stream ended, so the sidebar dropped the thread while the
    completion hook (e.g. GitHub push/PR publishing) was still running.
    """
    import agent.modules.agent_runtime.runner as runner_module
    from agent.modules.agent_runtime.active_sessions import (
        current_session_id_var,
        get_active_session_registry,
    )

    registry = get_active_session_registry()
    observed: dict = {}

    async def fake_run_agent_stream(**kwargs):
        observed["stream_session_id"] = current_session_id_var.get()
        observed["stream_active"] = any(
            session["thread_id"] == kwargs["thread_id"]
            for session in registry.list_active()
        )
        yield {"type": "final", "content": "done"}

    async def completion_hook(task: BackgroundTask) -> None:
        observed["hook_session_id"] = current_session_id_var.get()
        observed["hook_active"] = any(
            session["thread_id"] == task.thread_id
            for session in registry.list_active()
        )

    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)

    manager = BackgroundTaskManager()
    task_id = await manager.submit(
        "do work",
        agent_name="default",
        completion_hook=completion_hook,
    )
    task = await _wait_for_task_status(manager, task_id, "completed")

    assert observed["stream_active"] is True
    assert observed["hook_active"] is True
    assert observed["stream_session_id"] is not None
    assert observed["stream_session_id"] == observed["hook_session_id"]
    assert all(
        session["thread_id"] != task["thread_id"]
        for session in registry.list_active()
    )


@pytest.mark.asyncio
async def test_background_task_retry_leaves_no_active_session(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed attempt must not leak its session when the task retries."""
    import agent.modules.agent_runtime.runner as runner_module
    from agent.modules.agent_runtime.active_sessions import get_active_session_registry

    registry = get_active_session_registry()
    attempts = {"count": 0}

    async def flaky_run_agent_stream(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        yield {"type": "final", "content": "done"}

    monkeypatch.setattr(runner_module, "run_agent_stream", flaky_run_agent_stream)

    manager = BackgroundTaskManager()
    task_id = await manager.submit("do work", agent_name="default", max_retries=1)
    task = await _wait_for_task_status(manager, task_id, "completed")

    assert task["retry_count"] == 1
    assert attempts["count"] == 2

    # The failed attempt's session reference is released from its own
    # (still-finishing) execution context, which can lag slightly behind the
    # retried attempt's completion. Wait for the session to be cleaned up.
    for _ in range(100):
        if all(
            session["thread_id"] != task["thread_id"]
            for session in registry.list_active()
        ):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("session leaked after retried background task")


@pytest.mark.asyncio
async def test_background_task_cleanup_hook_runs_on_completion(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module

    async def fake_run_agent_stream(**kwargs):
        yield {"type": "final", "content": "done"}

    monkeypatch.setattr(runner_module, "run_agent_stream", fake_run_agent_stream)

    cleaned: list[str] = []

    async def cleanup_hook(task: BackgroundTask) -> None:
        cleaned.append(task.task_id)

    manager = BackgroundTaskManager()
    task_id = await manager.submit(
        "do work",
        agent_name="default",
        cleanup_hook=cleanup_hook,
    )
    await _wait_for_task_status(manager, task_id, "completed")

    assert cleaned == [task_id]


@pytest.mark.asyncio
async def test_background_task_cleanup_hook_runs_on_failure(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.agent_runtime.runner as runner_module

    async def failing_run_agent_stream(**kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover - keep this an async generator

    monkeypatch.setattr(runner_module, "run_agent_stream", failing_run_agent_stream)

    cleaned: list[str] = []

    async def cleanup_hook(task: BackgroundTask) -> None:
        cleaned.append(task.task_id)

    manager = BackgroundTaskManager()
    task_id = await manager.submit(
        "do work",
        agent_name="default",
        cleanup_hook=cleanup_hook,
    )
    await _wait_for_task_status(manager, task_id, "failed")

    assert cleaned == [task_id]


@pytest.mark.asyncio
async def test_background_task_cleanup_hook_waits_for_final_retry(
    background_task_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cleanup hook must not run between retry attempts."""
    import agent.modules.agent_runtime.runner as runner_module

    attempts = {"count": 0}

    async def flaky_run_agent_stream(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        yield {"type": "final", "content": "done"}

    monkeypatch.setattr(runner_module, "run_agent_stream", flaky_run_agent_stream)

    cleaned: list[str] = []

    async def cleanup_hook(task: BackgroundTask) -> None:
        cleaned.append(task.task_id)
        assert attempts["count"] == 2

    manager = BackgroundTaskManager()
    task_id = await manager.submit(
        "do work",
        agent_name="default",
        max_retries=1,
        cleanup_hook=cleanup_hook,
    )
    await _wait_for_task_status(manager, task_id, "completed")

    # The first attempt's execution context can flip ``_async_task`` to None
    # slightly before the retried attempt's finally block runs the cleanup
    # hook, so wait for the hook itself.
    for _ in range(100):
        if cleaned:
            break
        await asyncio.sleep(0.01)
    assert cleaned == [task_id]


@pytest.mark.asyncio
async def test_active_session_registry_reuses_session_per_thread() -> None:
    """Nested runs on the same thread share one reference-counted session."""
    from agent.modules.agent_runtime.active_sessions import (
        ActiveSession,
        ActiveSessionRegistry,
    )

    registry = ActiveSessionRegistry()

    def make_session(thread_id: str) -> ActiveSession:
        return ActiveSession(
            thread_id=thread_id,
            platform="task",
            user_id="dashboard",
            channel_id="abc",
            agent_name="default",
        )

    events: list[str] = []

    class FakeQueue:
        def put_nowait(self, event):
            events.append(event["type"])

    async def flush_broadcasts() -> None:
        # _broadcast schedules queue pushes via loop.call_soon_threadsafe.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    registry._listeners.add(FakeQueue())

    first = registry.acquire(make_session("thread-1"))
    second = registry.acquire(make_session("thread-1"))
    other = registry.acquire(make_session("thread-2"))
    await flush_broadcasts()

    assert first == second
    assert other != first
    assert registry.count() == 2
    assert events == ["session_started", "session_started"]

    registry.release(first)
    await flush_broadcasts()
    assert registry.count() == 2  # thread-1 still has one reference left
    assert events == ["session_started", "session_started"]

    registry.release(second)
    await flush_broadcasts()
    assert registry.count() == 1
    assert events == ["session_started", "session_started", "session_stopped"]

    registry.release(other)
    await flush_broadcasts()
    assert registry.count() == 0
    assert events == [
        "session_started",
        "session_started",
        "session_stopped",
        "session_stopped",
    ]
