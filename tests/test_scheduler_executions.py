"""Durable scheduled browser handoffs, recovery, and overlap admission."""

import json
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.errors import GraphDrained

from agent.modules.agent_runtime.async_subagents.repository import AsyncSubagentRepository
from agent.modules.agents import AgentConfig
from agent.modules.scheduler.executions import admit_run, cancel_run, get_run, resume_run, waiting_run
from agent.modules.scheduler.history import delete_run, list_runs, mark_interrupted_runs
from agent.modules.tools.builtin import browser as tools
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.base import BaseState
from agent.modules.workspaces import WorkspaceRef
from agent.modules.usage import build_usage_context
from test_async_subagents import repository, runtime_environment
from test_browser_sessions import browser


@pytest_asyncio.fixture
async def scheduled(runtime_environment, browser, monkeypatch, tmp_path):
    env = runtime_environment
    async def session():
        return browser.container._async_session_maker()
    env.repository = env.service.repository = AsyncSubagentRepository(session)
    env.agents["scheduler-executor"] = AgentConfig(name="scheduler-executor", graph_type="react_agent")
    monkeypatch.setattr(env.service, "start", AsyncMock())
    monkeypatch.setattr(tools, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.modules.agent_runtime.runner.load_usage_context",
        AsyncMock(side_effect=lambda thread, usage=None: build_usage_context(thread, usage)))
    notify = AsyncMock()
    monkeypatch.setattr("agent.modules.scheduler.service._send_notification", notify)

    async def handoff(state, config, runtime):
        assert config["configurable"]["approval_supported"]
        assert config["metadata"]["usage_context"]["platform"] == "bg"
        result = await tools._handoff(SimpleNamespace(config=config, context=runtime.context,
            tool_call_id="sign-in"), "Sign in to finish the scheduled task.")
        return {"messages": [AIMessage(content=result.content)]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("handoff", handoff)
    builder.add_edge(START, "handoff")
    builder.add_edge("handoff", END)
    env.current_graph[0] = builder.compile(checkpointer=InMemorySaver())

    async def admit(run_id="run-1", job_id="daily", *, platform="api", user_id="1"):
        from agent.modules.conversations import upsert_conversation_thread
        await upsert_conversation_thread(thread_id=f"scheduled-{run_id}", agent_name="scheduler-executor",
            kind="scheduled", platform="bg", user_id=user_id, title="Sign in")
        return await admit_run(env.service, run_id=run_id, thread_id=f"scheduled-{run_id}",
            job_id=job_id, task="Sign in", platform=platform, user_id=user_id, trigger_source="schedule",
            workspace=WorkspaceRef(locator=str(tmp_path)), browser_owner_id="admin:1",
            executor={"provider": "test", "model": "test"})

    async def execute(run_id="run-1"):
        job = await env.repository.claim(run_id, "worker")
        assert job
        return [event async for event in env.service._job_stream(job, f"stream-{job['attempt']}")]

    yield SimpleNamespace(env=env, browser=browser, admit=admit, execute=execute, notify=notify)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_browser", [False, True], ids=["model", "browser"])
async def test_scheduler_thread_loads_while_run_is_active(scheduled, monkeypatch, use_browser):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.delivery.http.dashboard.routes.conversations import router
    from agent.modules.admin_auth import get_current_admin

    f = scheduled
    thread_id = "scheduled-run-1"
    owner_id = "admin:1"
    started = asyncio.Event()
    release = asyncio.Event()
    await f.admit()

    async def slow_operation(**values):
        started.set()
        await release.wait()
        return "Done"

    if use_browser:
        browser_session = await f.browser.ensure(thread_id, owner_id)
        monkeypatch.setattr(browser_session.driver.facade, "run", slow_operation)

    async def work(state):
        if use_browser:
            await f.browser.execute(thread_id, owner_id, "browser-call", "browser_run", {"code": "return 'Done';"})
        else:
            await slow_operation()
        return {"messages": [AIMessage(content="Done")]}

    saver = InMemorySaver()
    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    f.env.current_graph[0] = builder.compile(checkpointer=saver)
    monkeypatch.setattr(f.env.container, "_checkpointer", saver)
    job = await f.env.repository.claim("run-1", "worker")
    assert job
    execution = asyncio.create_task(f.env.service._execute(job))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        monkeypatch.setattr(f.env.container, "_persistence_ready", True)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "1"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with asyncio.timeout(2):
                response = await client.get(f"/dashboard-api/chat-history/{thread_id}")
        assert response.status_code == 200
        assert response.json()["kind"] == "scheduled"
        assert response.json()["messages"][0]["content"] == "Sign in"
        assert not response.json().get("pending_browser_handoffs")
        assert (await get_run(f.env.service, "run-1", owner_id))["status"] == "running"
    finally:
        release.set()
        await execution


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True], ids=["direct", "queued"])
async def test_scheduler_reconnect_streams_while_model_is_running(scheduled, monkeypatch, tmp_path, managed):
    from contextlib import aclosing

    from fastapi import Request
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from agent.delivery.http.api.events_v2 import reconnect
    from agent.delivery.http.api.schemas import ReconnectRequest
    from agent.modules.agent_runtime.protocol import application_event
    from agent.modules.scheduler.service import execute_scheduled_task

    f = scheduled
    started = asyncio.Event()
    release = asyncio.Event()

    class PausingModel(FakeListChatModel):
        async def _astream(self, messages, **kwargs):
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
                started.set()
                await release.wait()

    model = PausingModel(responses=["Ready"])

    async def work(state):
        return {"messages": [await model.ainvoke(state["messages"])]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    f.env.current_graph[0] = builder.compile(checkpointer=InMemorySaver())
    if managed:
        await f.admit()
        job = await f.env.repository.claim("run-1", "worker")
        assert job
        execution = asyncio.create_task(f.env.service._execute(job))
    else:
        monkeypatch.setattr("agent.modules.workspaces.create_no_workspace",
                            AsyncMock(return_value=WorkspaceRef(locator=str(tmp_path))))
        execution = asyncio.create_task(execute_scheduled_task("telegram", "1", "Generate a result"))

    async def receive():
        await asyncio.Future()

    try:
        async with asyncio.timeout(5):
            await started.wait()
            [active] = f.env.container.active_session_registry.list_active()
            assert active["platform"] == "bg"
            response = await reconnect(ReconnectRequest(thread_id=active["thread_id"]),
                                       Request({"type": "http"}, receive))
            async with aclosing(response.body_iterator) as chunks:
                async for line in chunks:
                    event = application_event(json.loads(line)) if line.strip() else None
                    if event and event["type"] == "message":
                        assert event["content"] == "R"
                        assert not execution.done()
                        break
                else:
                    pytest.fail("The scheduled stream closed before delivering model output.")
    finally:
        release.set()
        await asyncio.wait_for(execution, timeout=5)

    [record] = (await list_runs())["runs"]
    assert record["status"] == "completed"
    assert record["response"] == "Ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["return", "cancel"])
async def test_scheduled_browser_waits_and_resumes_exact_execution(scheduled, decision):
    f = scheduled
    await f.admit()
    await f.execute()
    waiting = await waiting_run(f.env.service, "scheduled-run-1", "admin:1")
    assert waiting["status"] == "waiting"
    assert f.browser.sessions["scheduled-run-1"].handoff_id
    with pytest.raises(ValueError, match="running or waiting"):
        await delete_run("run-1")
    assert await mark_interrupted_runs() == 0
    pending = waiting["interrupts"][0]
    payload = {"action": "browser_handoff", "handoff_id": pending["value"]["handoff_id"], "decision": decision}
    for _ in range(2):
        assert (await resume_run(f.env.service, "run-1", pending["id"], payload, "admin:1"))["status"] == "pending"
    await f.execute()
    history = (await list_runs())["runs"][0]
    assert history["status"] == "completed" and history["finished_at"]
    receipt = json.loads(history["response"])
    assert receipt["handoff_status"] == ("returned" if decision == "return" else "cancelled")
    assert ("scheduled-run-1" in f.browser.sessions) is (decision == "return")
    f.notify.assert_awaited_once()
    assert await waiting_run(f.env.service, "scheduled-run-1", "admin:1") is None


@pytest.mark.asyncio
async def test_overlap_ticks_skip_until_waiting_run_is_cancelled(scheduled):
    f = scheduled
    await f.admit()
    await f.execute()
    assert (await f.admit("overlap"))["status"] == "skipped"
    assert (await list_runs())["runs"][0]["status"] == "skipped"
    with pytest.raises(LookupError):
        await cancel_run(f.env.service, "run-1", "admin:2")
    await cancel_run(f.env.service, "run-1", "admin:1")
    assert (await get_run(f.env.service, "run-1", "admin:1"))["status"] == "cancelled"
    assert not f.browser.sessions
    assert (await f.admit("next"))["status"] == "pending"


@pytest.mark.asyncio
async def test_stale_scheduler_responses_are_rejected(scheduled):
    f = scheduled
    await f.admit()
    await f.execute()
    pending = (await waiting_run(f.env.service, "scheduled-run-1", "admin:1"))["interrupts"][0]
    payload = {"action": "browser_handoff", "handoff_id": pending["value"]["handoff_id"], "decision": "return"}
    with pytest.raises(LookupError):
        await resume_run(f.env.service, "run-1", pending["id"], payload, "admin:2")
    with pytest.raises(LookupError):
        await waiting_run(f.env.service, "scheduled-run-1", "admin:2", strict=True)
    with pytest.raises(ValueError, match="interrupt"):
        await resume_run(f.env.service, "run-1", "stale", payload, "admin:1")
    with pytest.raises(ValueError, match="does not match"):
        await resume_run(f.env.service, "run-1", pending["id"], {**payload, "handoff_id": "stale"}, "admin:1")
    assert f.browser.sessions["scheduled-run-1"].handoff_id == payload["handoff_id"]


@pytest.mark.asyncio
async def test_saved_handoff_remains_resumable_after_browser_loss(scheduled):
    f = scheduled
    await f.admit()
    await f.execute()
    await f.browser.release_thread("scheduled-run-1")
    pending = (await waiting_run(f.env.service, "scheduled-run-1", "admin:1"))["interrupts"][0]
    await resume_run(f.env.service, "run-1", pending["id"], {"action": "browser_handoff",
        "handoff_id": pending["value"]["handoff_id"], "decision": "return"}, "admin:1")
    await f.execute()
    response = json.loads((await list_runs())["runs"][0]["response"])
    assert response["handoff_status"] == "session_lost"
    assert not f.browser.sessions


@pytest.mark.asyncio
async def test_dispatcher_releases_execution_capacity_while_scheduler_waits(scheduled, monkeypatch):
    from agent.modules.agent_runtime.async_subagents.service import AsyncSubagentService

    f = scheduled
    read = f.env.container.config_service.get_int
    monkeypatch.setattr(f.env.container.config_service, "get_int", lambda key, default=None:
        1 if key == "subagents.max_concurrency" else read(key, default))
    await f.admit()
    await AsyncSubagentService.start(f.env.service)

    async def until_status(run_id, status):
        async with asyncio.timeout(8):
            while (await get_run(f.env.service, run_id, "admin:1"))["status"] != status:
                await asyncio.sleep(0.02)

    await until_status("run-1", "waiting")
    await f.admit("run-2", "other-job")
    await until_status("run-2", "waiting")
    pending = (await waiting_run(f.env.service, "scheduled-run-1", "admin:1"))["interrupts"][0]
    await resume_run(f.env.service, "run-1", pending["id"], {"action": "browser_handoff",
        "handoff_id": pending["value"]["handoff_id"], "decision": "return"}, "admin:1")
    await until_status("run-1", "completed")
    await cancel_run(f.env.service, "run-2", "admin:1")
    assert not f.browser.sessions["scheduled-run-1"].closed
    assert "scheduled-run-2" not in f.browser.sessions


@pytest.mark.asyncio
async def test_lease_recovery_preserves_a_checkpointed_scheduler_handoff(scheduled, monkeypatch):
    f = scheduled
    await f.admit()
    graph = f.env.current_graph[0]

    async def interrupted_read(config):
        state = await graph.aget_state(config)
        if f.env.service._interrupts(state):
            raise GraphDrained()
        return state

    with monkeypatch.context() as patch:
        patch.setattr("agent.modules.workflows.get_workflow_graph", lambda _: SimpleNamespace(aget_state=interrupted_read))
        await f.execute()
    saved = await get_run(f.env.service, "run-1", "admin:1")
    assert saved["status"] == "running" and saved["lease_until"] == 0
    assert await mark_interrupted_runs() == 0
    assert not f.browser.sessions
    assert await f.browser.repository.pending_handoff("scheduled-run-1")
    await f.execute()
    assert (await waiting_run(f.env.service, "scheduled-run-1", "admin:1"))["status"] == "waiting"
    assert (await get_run(f.env.service, "run-1", "admin:1"))["attempt"] == 2
    f.notify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["return", "cancel", None], ids=["continue", "cancel-handoff", "cancel-run"])
async def test_scheduler_http_controls_resolve_only_the_owned_expired_run(scheduled, decision, monkeypatch):
    import time

    import httpx
    from fastapi import FastAPI
    from agent.delivery.http.dashboard.router import router
    from agent.delivery.http.dashboard.routes import browser as browser_routes
    from agent.modules.admin_auth import get_current_admin

    f = scheduled
    await f.admit()
    await f.execute()
    app = FastAPI()
    app.include_router(router)
    admin = ["1"]
    app.dependency_overrides[get_current_admin] = lambda: admin[0]
    app.dependency_overrides[browser_routes.service] = lambda: f.browser
    endpoint = "/scheduler/runs/run-1"
    browser_endpoint = "/dashboard-api/browser/threads/scheduled-run-1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        status = (await client.get(browser_endpoint)).json()
        assert status["state"] == "waiting_for_human" and status["source"] == "scheduler"
        deadline = status["handoff"]["expires_at"]
        with monkeypatch.context() as patch:
            patch.setattr(time, "time", lambda: deadline + 1)
            status = (await client.get(browser_endpoint)).json()
        assert not status["open"] and status["handoff"]["expired"]
        assert status["scheduled_run"]["status"] == "waiting"
        pending = status["scheduled_run"]["interrupts"][0]
        body = {"interrupt_id": pending["id"], "payload": {"action": "browser_handoff",
            "handoff_id": pending["value"]["handoff_id"], "decision": decision or "return"}}
        assert (await client.post(endpoint + "/resume", json={**body, "interrupt_id": "stale"})).status_code == 409
        admin[0] = "2"
        assert (await client.post(endpoint + "/resume", json=body)).status_code == 404
        assert (await client.post(endpoint + "/cancel")).status_code == 404
        admin[0] = "1"
        if decision is None:
            for _ in range(2):
                assert (await client.post(endpoint + "/cancel")).status_code == 202
        else:
            for _ in range(2):
                assert (await client.post(endpoint + "/resume", json=body)).status_code == 202
            await f.execute()
        run = (await client.get("/scheduler/runs")).json()["runs"][0]
        assert run["status"] == ("cancelled" if decision is None else "completed")
        assert run["finished_at"]
        if decision:
            receipt = json.loads(run["response"])
            assert receipt["handoff_status"] == ("expired" if decision == "return" else "cancelled")
        assert (await client.get(browser_endpoint)).json()["handoff"] is None
    assert not f.browser.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/api/chat", "/v1/api/chat/stream"])
@pytest.mark.parametrize("status", ["pending", "running", "waiting"])
async def test_legacy_chat_cannot_bypass_a_managed_scheduled_run(scheduled, monkeypatch, path, status):
    import importlib
    import httpx
    from fastapi import FastAPI
    from agent.modules.admin_auth import get_current_admin

    f = scheduled
    await f.admit()
    if status == "running":
        await f.env.repository.claim("run-1", "worker")
    elif status == "waiting":
        await f.execute()
    graph = f.env.current_graph[0]
    config = {"configurable": {"thread_id": "scheduled-run-1"}}
    before = await graph.aget_state(config)
    payload = {"message": "Continue", "thread_id": "scheduled-run-1", "agent_name": "parent"}
    if status == "waiting":
        pending = f.env.service._interrupts(before)[0]
        payload.update(message="", resume=True, resume_payload={"action": "browser_handoff",
            "handoff_id": pending["value"]["handoff_id"], "decision": "return"})

    api = importlib.import_module("agent.delivery.http.api.router")

    async def unexpected_run(*args, **kwargs):
        pytest.fail("Legacy chat must reject managed scheduled runs before preparing execution.")

    app = FastAPI()
    app.include_router(api.router)
    admin = ["1"]
    app.dependency_overrides[get_current_admin] = lambda: admin[0]
    with monkeypatch.context() as patch:
        patch.setattr(f.env.container, "_persistence_ready", True)
        patch.setattr(api, "prepare_run_params", unexpected_run)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(path, json=payload)
            assert response.status_code == 409
            assert "scheduler API" in response.json()["detail"]
            admin[0] = "2"
            assert (await client.post(path, json=payload)).status_code == 404

    assert (await get_run(f.env.service, "run-1", "admin:1"))["status"] == status
    after = await graph.aget_state(config)
    assert after.config == before.config and after.values == before.values
    assert f.env.service._interrupts(after) == f.env.service._interrupts(before)
    if status == "waiting":
        assert await f.browser.repository.pending_handoff("scheduled-run-1")
        await resume_run(f.env.service, "run-1", pending["id"], payload["resume_payload"], "admin:1")
        await f.execute()
        assert (await get_run(f.env.service, "run-1", "admin:1"))["status"] == "completed"
        assert (await f.admit("next"))["status"] == "pending"
