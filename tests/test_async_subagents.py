"""Durable admission, FIFO steering, recovery fencing, and notification delivery."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.modules.agent_runtime.async_subagents.models import (
    SubagentExecutionRecord, SubagentNotificationRecord, SubagentTaskRecord,
)
from agent.modules.agent_runtime.async_subagents.repository import AsyncSubagentRepository
from agent.shared.async_subagents import merge_event_cursors, merge_task_references
from agent.shared.infrastructure.db.base import Base


@pytest_asyncio.fixture
async def repository(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'queue.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[
            SubagentTaskRecord.__table__, SubagentExecutionRecord.__table__, SubagentNotificationRecord.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        return factory()

    yield AsyncSubagentRepository(session)
    await engine.dispose()


async def admit(repository, *, key="launch", parent="api_parent"):
    return await repository.create_task(key=key, parent_thread_id=parent, tool_call_id=key,
        parent_message_id="message", agent_name="worker", task="Do the work.",
        context={"agent_name": "worker", "workflow": "react_agent"},
        parent_context={"agent_name": "parent", "workflow": "react_agent", "delegated_agent_name": "worker"})


@pytest.mark.asyncio
async def test_admission_is_idempotent_and_scoped(repository):
    first, replay = await asyncio.gather(admit(repository), admit(repository))
    assert first["task_id"] == replay["task_id"]
    assert len(await repository.candidates()) == 1
    assert await repository.get_task("another_parent", first["task_id"]) is None
    listed = await repository.list_tasks("api_parent")
    assert listed["total"] == 1
    assert "context" not in listed["tasks"][0]
    assert "result" not in listed["tasks"][0]


@pytest.mark.asyncio
async def test_updates_are_fifo_and_waiting_requests_are_preserved(repository):
    task = await admit(repository)
    initial = (await repository.candidates())[0]
    job = await repository.claim(initial["job_id"], "worker")
    await repository.enqueue_update("api_parent", task["task_id"], "First follow-up.", "update-1")
    await repository.enqueue_update("api_parent", task["task_id"], "First follow-up.", "update-1")
    await repository.enqueue_update("api_parent", task["task_id"], "Second follow-up.", "update-2")
    await repository.finish(job, "waiting", interrupts=[{"id": "question", "value": {"type": "user_input_request"}}])
    assert await repository.candidates() == []
    current = await repository.get_task("api_parent", task["task_id"])
    assert current["queued_updates"] == 2
    with pytest.raises(ValueError):
        await repository.resume_task("api_parent", task["task_id"], job["job_id"], "stale", {"action": "answer"})
    await repository.resume_task("api_parent", task["task_id"], job["job_id"], "question", {"action": "answer"})
    resumed = await repository.claim(job["job_id"], "worker")
    await repository.finish(resumed, "completed", result="Initial result.")
    candidates = await repository.candidates()
    assert [row["input"]["text"] for row in candidates] == ["First follow-up."]


@pytest.mark.asyncio
async def test_expired_worker_is_fenced_and_result_is_not_lost(repository):
    task = await admit(repository)
    candidate = (await repository.candidates())[0]
    first = await repository.claim(candidate["job_id"], "old-worker")
    assert await repository.claim(candidate["job_id"], "other-worker") is None
    await repository.heartbeat(first, release=True)
    recovered = await repository.claim(candidate["job_id"], "new-worker")
    assert await repository.finish(first, "completed", result="Late old result.") is None
    await repository.finish(recovered, "completed", result="Recovered result.")
    current = await repository.get_task("api_parent", task["task_id"])
    assert current["result"] == "Recovered result."
    events = await repository.pending_notifications("api_parent")
    assert len(events) == 1
    assert events[0]["payload"]["result"] == "Recovered result."
    await repository.acknowledge("api_parent", {task["task_id"]: events[0]["version"]})
    assert await repository.pending_notifications("api_parent") == []


@pytest.mark.asyncio
async def test_cancel_tree_fences_late_results_and_suppresses_wakeups(repository):
    task = await admit(repository)
    child = await admit(repository, key="nested", parent=task["thread_id"])
    jobs = [await repository.claim(row["job_id"], "worker") for row in await repository.candidates()]
    await repository.cancel_scope("api_parent")
    for job in jobs:
        assert await repository.finish(job, "completed", result="Too late.") is None
    assert (await repository.get_task("api_parent", task["task_id"]))["status"] == "cancelled"
    assert (await repository.get_task(task["thread_id"], child["task_id"]))["status"] == "cancelled"
    assert await repository.candidates() == []
    assert await repository.pending_notifications("api_parent") == []


@pytest.mark.asyncio
async def test_wakeups_are_coalesced_but_waiting_does_not_wake_parent(repository):
    first = await admit(repository)
    second = await admit(repository, key="second")
    for candidate in await repository.candidates():
        job = await repository.claim(candidate["job_id"], "worker")
        await repository.finish(job, "completed", result="Done.")
    await repository.prepare_wakeups()
    await repository.prepare_wakeups()
    wakeups = [job for job in await repository.candidates() if job["kind"] == "wakeup"]
    assert len(wakeups) == 1
    await repository.acknowledge("api_parent", {first["task_id"]: 100, second["task_id"]: 100})
    assert await repository.candidates() == []
    waiting = await admit(repository, key="waiting")
    job = await repository.claim((await repository.candidates())[0]["job_id"], "worker")
    await repository.finish(job, "waiting", interrupts=[{"id": "ask", "value": "Question"}])
    await repository.prepare_wakeups()
    assert await repository.candidates() == []
    assert (await repository.get_task("api_parent", waiting["task_id"]))["status"] == "waiting"


def test_checkpoint_reducers_keep_newer_state_and_cursors():
    assert merge_task_references({"a": {"version": 3}}, {"a": {"version": 2}}) == {"a": {"version": 3}}
    assert merge_event_cursors({"a": 3}, {"a": 2, "b": 1}) == {"a": 3, "b": 1}


@pytest.mark.asyncio
async def test_blocked_updates_do_not_starve_other_threads(repository):
    task = await admit(repository)
    job = await repository.claim((await repository.candidates())[0]["job_id"], "worker")
    await repository.finish(job, "waiting", interrupts=[{"id": "approval", "value": {}}])
    for index in range(100):
        await repository.enqueue_update("api_parent", task["task_id"], "Follow up", f"update-{index}")
    unrelated = await admit(repository, key="unrelated", parent="api_unrelated")
    candidates = await repository.candidates()
    assert [job["task_id"] for job in candidates] == [unrelated["task_id"]]
    assert (await repository.get_task("api_parent", task["task_id"]))["queued_updates"] == 100


@pytest.mark.asyncio
async def test_waiting_notifications_do_not_starve_completion_wakeups(repository):
    for index in range(100):
        task = await admit(repository, key=f"waiting-{index}", parent=f"api_waiting_{index}")
        job = await repository.claim(task["run_id"], "worker")
        await repository.finish(job, "waiting", interrupts=[{"id": "approval", "value": {}}])
    completed = await admit(repository, key="completed", parent="api_completed")
    job = await repository.claim(completed["run_id"], "worker")
    await repository.finish(job, "completed", result="Done")
    await repository.prepare_wakeups()
    wakeups = await repository.candidates()
    assert [(job["thread_id"], job["kind"]) for job in wakeups] == [("api_completed", "wakeup")]
    await repository.prepare_wakeups()
    assert len(await repository.candidates()) == 1


@pytest.mark.asyncio
async def test_active_task_list_keeps_live_states_and_supports_pagination(repository):
    for status in ("pending", "running", "waiting", "completed"):
        task = await admit(repository, key=status)
        if status != "pending":
            job = await repository.claim(task["run_id"], "worker")
            if status != "running":
                await repository.finish(job, status, result="Done")
    listed = await repository.list_tasks("api_parent", status="active", limit=2)
    assert listed["total"] == listed["active_count"] == 3
    rest = await repository.list_tasks("api_parent", status="active", limit=2, offset=2)
    assert {task["status"] for task in [*listed["tasks"], *rest["tasks"]]} == {"pending", "running", "waiting"}
    assert all(task["delegation_id"] == task["task_id"] for task in listed["tasks"])


@pytest_asyncio.fixture
async def runtime_environment(repository, isolated_container, monkeypatch):
    from unittest.mock import AsyncMock
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langchain_core.messages import AIMessage
    from agent.modules.agent_runtime import runner
    from agent.modules.agent_runtime.async_subagents.service import AsyncSubagentService
    from agent.modules.agents import AgentConfig
    from agent.modules.usage import build_usage_context
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workflows.state.base import BaseState

    calls = []
    agents = {name: AgentConfig(name=name, graph_type="react_agent", sub_agents=["worker"] if name == "parent" else [],
                               system_prompt="Complete the task.") for name in ("parent", "worker")}
    isolated_container._agent_catalog_service = SimpleNamespace(
        get_agent=agents.get, validate_call=lambda caller, target: caller == "parent" and target == "worker")
    monkeypatch.setattr(runner, "_assert_thread_graph_family", AsyncMock())
    monkeypatch.setattr(runner, "_record_conversation_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "load_usage_context", AsyncMock(return_value=build_usage_context("api_parent", {"platform": "api"})))

    async def work(state, runtime):
        calls.append(runtime.context.agent_name)
        return {"messages": [AIMessage(content=f"Result from {runtime.context.agent_name}")]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    current_graph = [graph]
    monkeypatch.setattr(runner, "get_workflow_graph", lambda _: current_graph[0])
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda _: current_graph[0])
    service = AsyncSubagentService(isolated_container, repository)
    isolated_container._async_subagents = service
    yield SimpleNamespace(service=service, repository=repository, container=isolated_container,
                          graph=graph, current_graph=current_graph, calls=calls, agents=agents)
    await service.shutdown()
    await isolated_container.chat_stream_manager.shutdown()


@pytest.mark.asyncio
async def test_real_runtime_completes_child_and_automatically_wakes_parent(runtime_environment):
    env = runtime_environment
    task = await admit(env.repository)
    await env.service.start()
    async with asyncio.timeout(8):
        while len(env.calls) < 2:
            await asyncio.sleep(0.02)
        while await env.repository.pending_notifications("api_parent"):
            await asyncio.sleep(0.02)
    assert env.calls == ["worker", "parent"]
    assert (await env.service.get("api_parent", task["task_id"]))["status"] == "completed"
    snapshot = await env.graph.aget_state({"configurable": {"thread_id": "api_parent"}})
    notices = [message for message in snapshot.values["messages"] if message.additional_kwargs.get("source") == "async_subagent"]
    assert len(notices) == 1
    assert snapshot.values["async_event_cursors"][task["task_id"]] > 0


@pytest.mark.asyncio
async def test_recovery_resumes_saved_superstep_and_does_not_replay_completed_work(runtime_environment, tmp_path):
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from agent.modules.agent_runtime.async_subagents.service import AsyncSubagentService
    from langgraph.graph import END, START, StateGraph
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workflows.state.base import BaseState

    env, calls = runtime_environment, []

    async def first(state):
        calls.append("first")
        return {}

    async def second(state):
        calls.append("second")
        return {"messages": [AIMessage(content="Recovered") ]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    task = await admit(env.repository)
    job = await env.repository.claim((await env.repository.candidates())[0]["job_id"], "before-restart")
    config = {"configurable": {"thread_id": task["thread_id"]}, "metadata": {"k41_execution_id": job["job_id"]}}
    checkpoint_path = str(tmp_path / "checkpoints.db")
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"messages": [HumanMessage(content="Work", id=f"async-input:{job['job_id']}")]},
                            config, durability="sync", interrupt_after=["first"])
    await env.repository.heartbeat(job, release=True)
    recovered = await env.repository.claim(job["job_id"], "after-restart")
    replacement = AsyncSubagentService(env.container, env.repository)
    env.container._async_subagents = replacement
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        env.current_graph[0] = builder.compile(checkpointer=saver)
        events = [event async for event in replacement._job_stream(recovered, "recovered-stream")]
    await replacement.shutdown()
    assert events
    assert calls == ["first", "second"]
    assert (await env.service.get("api_parent", task["task_id"]))["result"] == "Recovered"


@pytest.mark.asyncio
async def test_recovery_finalizes_already_completed_checkpoint_without_model_call(runtime_environment):
    from langchain_core.messages import HumanMessage

    env = runtime_environment
    task = await admit(env.repository)
    job = await env.repository.claim((await env.repository.candidates())[0]["job_id"], "old")
    from agent.modules.workflows.run_config import WorkflowContext
    await env.graph.ainvoke({"messages": [HumanMessage(content="Work")]}, {
        "configurable": {"thread_id": task["thread_id"]}, "metadata": {"k41_execution_id": job["job_id"]}},
        context=WorkflowContext(agent_name="worker"), durability="sync")
    assert env.calls == ["worker"]
    await env.repository.heartbeat(job, release=True)
    recovered = await env.repository.claim(job["job_id"], "new")
    assert [event async for event in env.service._job_stream(recovered, "new-stream")] == []
    assert env.calls == ["worker"]
    assert (await env.service.get("api_parent", task["task_id"]))["status"] == "completed"
    assert len(await env.repository.pending_notifications("api_parent")) == 1


@pytest.mark.asyncio
async def test_waiting_child_resumes_on_exact_interrupt_without_dropping_queued_update(runtime_environment):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workflows.state.base import BaseState

    env = runtime_environment

    async def ask(state):
        answer = interrupt({"type": "user_input_request", "questions": []})
        return {"messages": [AIMessage(content=answer.get("summary", "Answered"))]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    env.current_graph[0] = builder.compile(checkpointer=InMemorySaver())
    task = await admit(env.repository)
    job = await env.repository.claim((await env.repository.candidates())[0]["job_id"], "worker")
    _ = [event async for event in env.service._job_stream(job, "waiting-stream")]
    waiting = await env.service.get("api_parent", task["task_id"])
    assert waiting["status"] == "waiting"
    await env.repository.enqueue_update("api_parent", task["task_id"], "Next", "next")
    await env.service.resume("api_parent", task["task_id"], waiting["run_id"], waiting["interrupts"][0]["id"],
                             {"action": "answer", "answers": [], "summary": "Approved details"})
    resumed = await env.repository.claim(job["job_id"], "worker")
    _ = [event async for event in env.service._job_stream(resumed, "resumed-stream")]
    current = await env.service.get("api_parent", task["task_id"])
    assert current["result"] == "Approved details"
    assert current["queued_updates"] == 1
    assert (await env.repository.candidates())[0]["input"]["text"] == "Next"


@pytest.mark.asyncio
async def test_http_api_scopes_tasks_and_requires_idempotency_key(runtime_environment):
    import httpx
    from fastapi import FastAPI
    from agent.delivery.http.api.events_v2 import router
    from agent.modules.admin_auth import get_current_admin

    env = runtime_environment
    task = await admit(env.repository)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    base = f"/v2/api/chat/api_parent/async-tasks/{task['task_id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(base.replace("api_parent", "another_parent"))).status_code == 404
        assert (await client.post(base + "/updates", json={"instructions": "More"})).status_code == 422
        body = {"instructions": "More", "request_id": "stable-request"}
        assert (await client.post(base + "/updates", json=body)).status_code == 202
        assert (await client.post(base + "/updates", json=body)).status_code == 202
        assert (await client.get(base)).json()["queued_updates"] == 1
        assert (await client.get("/v2/api/chat/api_parent/async-tasks?limit=0")).status_code == 422
        active = await client.get("/v2/api/chat/api_parent/async-tasks?status=active")
        assert active.status_code == 200
        assert active.json()["tasks"][0]["delegation_id"] == task["task_id"]
        assert (await client.post(base + "/cancel")).json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_start_tool_returns_before_child_finishes_and_replay_reuses_task(runtime_environment, tmp_path):
    from langchain_core.messages import AIMessage
    from langgraph.types import Command
    from agent.modules.tools.builtin.delegation.async_tasks import start_async_task
    from agent.modules.workflows.run_config import WorkflowContext

    env = runtime_environment
    runtime = SimpleNamespace(context=WorkflowContext(agent_name="parent", working_dir=str(tmp_path)),
        config={"configurable": {"thread_id": "api_parent", "async_subagents_supported": True}},
        state={"messages": [AIMessage(content="", id="launch-message")]}, tool_call_id="launch-tool")
    result = await start_async_task.coroutine(task="Work independently.", sub_agent="worker", runtime=runtime)
    assert isinstance(result, Command)
    assert env.calls == []
    reference = next(iter(result.update["async_tasks"].values()))
    replay = await start_async_task.coroutine(task="Work independently.", sub_agent="worker", runtime=runtime)
    assert next(iter(replay.update["async_tasks"])) == reference["task_id"]
    assert result.update["messages"][0].artifact["subagent"]["execution_mode"] == "async"


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [False, True])
async def test_async_subagents_cannot_launch_children_even_with_configured_targets(runtime_environment, tmp_path, metadata):
    from langchain_core.messages import AIMessage
    from agent.modules.workflows.run_config import WorkflowContext

    env = runtime_environment
    env.agents["worker"].sub_agents = ["parent"]
    config = {"configurable": {"thread_id": "child" if metadata else "api_parent:sub:child",
                               "async_subagents_supported": True}}
    if metadata:
        config["metadata"] = {"k41_parent_thread_id": "api_parent"}
    runtime = SimpleNamespace(context=WorkflowContext(agent_name="worker", working_dir=str(tmp_path)),
        config=config, state={"messages": [AIMessage(content="", id="nested")]}, tool_call_id="nested")
    with pytest.raises(PermissionError, match="Subagents cannot delegate"):
        await env.service.submit(task="Nested work", sub_agent="parent", runtime=runtime)
    assert await env.repository.candidates() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("with_project", [False, True])
async def test_start_tool_with_initialized_persistence_preserves_workspace(
    runtime_environment, tmp_path, monkeypatch, with_project,
):
    from unittest.mock import AsyncMock
    from langchain_core.messages import AIMessage
    from langgraph.types import Command
    from agent.modules.projects import add_project, get_project_workspace_service
    from agent.modules.tools.builtin.delegation.async_tasks import start_async_task
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workspaces import WorkspaceRef, remember_thread_workspace_ref

    env = runtime_environment
    original_get = env.container.config_service.get
    paths = {"workspace.project_storage_root": str(tmp_path / "project-storage"),
             "browser.storage_root": str(tmp_path / "browser"),
             "tools.storage_root": str(tmp_path / "tools")}
    env.container.agent_catalog_service.list_agent_cards = lambda: []
    monkeypatch.setattr(env.container.config_service, "get",
                        lambda key, default=None: paths[key] if key in paths else original_get(key, default))
    monkeypatch.setattr(env.service, "start", AsyncMock())
    workspace = tmp_path / "parent-workspace"
    workspace.mkdir()
    reference = WorkspaceRef(locator=str(workspace))
    context = WorkflowContext(agent_name="parent", workspace=reference, browser_owner_id="admin:regression")
    expected_workspace = context.workspace.model_dump()
    try:
        await env.container.initialize_persistence()
        assert env.container._persistence_ready
        if with_project:
            project = await add_project(reference)
            binding = await get_project_workspace_service(env.container).ensure(project["id"])
            await remember_thread_workspace_ref("api_parent", binding)
            expected_workspace = binding.model_dump()
        runtime = SimpleNamespace(
            context=context,
            config={"configurable": {"thread_id": "api_parent", "async_subagents_supported": True}},
            state={"messages": [AIMessage(content="", id="persisted-launch")]}, tool_call_id="persisted-launch-tool",
        )
        result = await start_async_task.coroutine(task="Open a page and scroll down.", sub_agent="worker", runtime=runtime)
        assert isinstance(result, Command), result
        task_id = next(iter(result.update["async_tasks"]))
        saved = await env.service.get("api_parent", task_id, private=True)
        assert saved["status"] == "pending"
        assert saved["context"]["workspace"] == expected_workspace
        assert saved["parent_context"]["workspace"] == expected_workspace
        assert saved["context"]["browser_owner_id"] == "admin:regression"
        assert env.calls == []
    finally:
        await env.container.close_persistence()


@pytest.mark.asyncio
async def test_parent_interrupt_defers_wakeup_and_idle_admission_keeps_user_running(runtime_environment):
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workflows.state.base import BaseState

    env = runtime_environment
    builder = StateGraph(BaseState, context_schema=WorkflowContext)

    async def ask(state):
        interrupt("User must answer")
        return {}

    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    env.current_graph[0] = graph
    await graph.ainvoke({"messages": [HumanMessage(content="Question")]}, {"configurable": {"thread_id": "api_parent"}})
    assert not await env.service._parent_ready({"thread_id": "api_parent", "context": {"workflow": "react_agent"}})
    started, release = asyncio.Event(), asyncio.Event()

    async def user_run(**params):
        started.set()
        await release.wait()
        if False:
            yield {}

    manager = env.container.chat_stream_manager
    session = await manager.get_or_create_session("api_busy", {}, run_fn=user_run)
    await started.wait()
    assert await manager.start_if_idle("api_busy", {}, run_fn=user_run) is None
    assert not session.task.cancelled()
    release.set()
    await session.task


@pytest.mark.asyncio
@pytest.mark.parametrize("already_received", [False, True])
async def test_wakeup_continues_an_unfinished_parent_checkpoint(runtime_environment, already_received):
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workflows.state.base import BaseState

    env, calls = runtime_environment, []

    async def first(state):
        calls.append("first")
        return {}

    async def second(state):
        calls.append("second")
        return {"messages": [AIMessage(content="Parent finished")]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    env.current_graph[0] = graph
    task = await admit(env.repository)
    child_job = await env.repository.claim((await env.repository.candidates())[0]["job_id"], "worker")
    await env.repository.finish(child_job, "completed", result="Child result")
    event = (await env.repository.pending_notifications("api_parent"))[0]
    initial = {"messages": [HumanMessage(content="Unfinished parent request")]}
    if already_received:
        initial["async_event_cursors"] = {task["task_id"]: event["version"]}
    await graph.ainvoke(initial, {"configurable": {"thread_id": "api_parent"}},
                        durability="sync", interrupt_after=["first"])
    await env.repository.prepare_wakeups()
    job = await env.repository.claim((await env.repository.candidates())[0]["job_id"], "worker")
    _ = [event async for event in env.service._job_stream(job, "wake-stream")]
    assert calls == ["first", "second"]
    assert await env.repository.pending_notifications("api_parent") == []


@pytest.mark.asyncio
async def test_plan_approval_changes_executor_and_preserves_queued_work(runtime_environment):
    from agent.modules.agents import AgentConfig

    env = runtime_environment
    env.agents["implementer"] = AgentConfig(name="implementer", graph_type="decision_agent")
    env.container.agent_catalog_service.get_agent_card = lambda name: SimpleNamespace(
        valid=True, internal=False, plan_approval_targets=[]) if name in env.agents else None
    task = await admit(env.repository)
    job = await env.repository.claim((await env.repository.candidates())[0]["job_id"], "worker")
    await env.repository.finish(job, "waiting", interrupts=[{"id": "plan", "value": {"type": "plan_review"}}])
    await env.repository.enqueue_update("api_parent", task["task_id"], "Continue afterwards", "after-plan")
    payload = {"action": "approve", "target_agent": "implementer"}
    await env.service.resume("api_parent", task["task_id"], job["job_id"], "plan", payload)
    await env.service.resume("api_parent", task["task_id"], job["job_id"], "plan", payload)
    resumed = await env.repository.claim(job["job_id"], "worker")
    assert resumed["context"]["workflow"] == "decision_agent"
    await env.repository.finish(resumed, "completed", result="Implemented", context=resumed["context"])
    next_run = (await env.repository.candidates())[0]
    assert next_run["context"]["agent_name"] == "implementer"
    assert next_run["input"]["text"] == "Continue afterwards"
    await env.service.update("api_parent", task["task_id"], "Another follow-up", "after-handoff")


@pytest.mark.asyncio
async def test_execution_timeout_is_terminal_and_notifies_parent(runtime_environment, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workflows.state.base import BaseState

    env = runtime_environment
    read_int = env.container.config_service.get_int
    monkeypatch.setattr(env.container.config_service, "get_int",
        lambda key, default=None: 0.05 if key == "subagents.run_timeout_seconds" else read_int(key, default))

    async def slow(state):
        await asyncio.sleep(10)
        return {}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("slow", slow)
    builder.add_edge(START, "slow")
    builder.add_edge("slow", END)
    env.current_graph[0] = builder.compile(checkpointer=InMemorySaver())
    task = await admit(env.repository)
    job = await env.repository.claim((await env.repository.candidates())[0]["job_id"], "worker")
    with pytest.raises(TimeoutError):
        _ = [event async for event in env.service._job_stream(job, "timeout-stream")]
    assert (await env.service.get("api_parent", task["task_id"]))["status"] == "timed_out"
    assert (await env.repository.pending_notifications("api_parent"))[0]["payload"]["event_status"] == "timed_out"
