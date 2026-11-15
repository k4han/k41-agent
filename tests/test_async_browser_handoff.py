"""A background browser can wait for its user without interrupting the parent."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphDrained
from langgraph.graph import END, START, StateGraph

from agent.modules.tools.builtin import browser as tools
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.base import BaseState
from test_async_subagents import repository, runtime_environment
from test_browser_sessions import browser


@pytest_asyncio.fixture
async def browser_task(runtime_environment, browser, monkeypatch):
    env = runtime_environment
    monkeypatch.setattr(tools, "authorize", lambda *args, **kwargs: None)

    async def handoff(state, config, runtime):
        result = await tools._handoff(SimpleNamespace(config=config, context=runtime.context,
            tool_call_id="browser-handoff"), "Sign in to continue the background task.")
        return {"messages": [AIMessage(content=result.content)]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("handoff", handoff)
    builder.add_edge(START, "handoff")
    builder.add_edge("handoff", END)
    env.current_graph[0] = builder.compile(checkpointer=InMemorySaver())
    task = await env.repository.create_task(key="browser-launch", parent_thread_id="api_parent",
        tool_call_id="launch", parent_message_id="message", agent_name="worker", task="Sign in.",
        context={"agent_name": "worker", "workflow": "react_agent", "browser_owner_id": "admin:1"},
        parent_context={"agent_name": "parent", "workflow": "react_agent", "delegated_agent_name": "worker"})
    parent_session = await browser.ensure("api_parent", "admin:1")
    return SimpleNamespace(env=env, browser=browser, task=task, parent=parent_session)


@pytest_asyncio.fixture
async def waiting_browser(browser_task):
    f = browser_task
    job = await f.env.repository.claim(f.task["run_id"], "worker")
    _ = [event async for event in f.env.service._job_stream(job, "browser-waiting")]
    waiting = await f.env.service.get("api_parent", f.task["task_id"])
    assert waiting["status"] == "waiting"
    pending = waiting["interrupts"][0]
    assert pending["value"]["type"] == "browser_handoff_request"
    assert pending["value"]["thread_id"] == f.task["thread_id"]
    assert f.browser.sessions[f.task["thread_id"]].handoff_id == pending["value"]["handoff_id"]
    assert f.parent.handoff_id is None
    return SimpleNamespace(env=f.env, browser=f.browser, task=waiting, pending=pending, parent=f.parent)


async def interrupt_browser_job(f, monkeypatch, interruption):
    job = await f.env.repository.claim(f.task["run_id"], "before-restart")
    graph = f.env.current_graph[0]

    async def read_state(config):
        snapshot = await graph.aget_state(config)
        if f.env.service._interrupts(snapshot):
            raise interruption()
        return snapshot

    # Interrupt the queue's status read after the graph has saved its handoff.
    with monkeypatch.context() as patch:
        patch.setattr("agent.modules.workflows.get_workflow_graph", lambda _: SimpleNamespace(aget_state=read_state))
        if interruption is GraphDrained:
            _ = [event async for event in f.env.service._job_stream(job, "browser-interrupted")]
        else:
            with pytest.raises(interruption):
                _ = [event async for event in f.env.service._job_stream(job, "browser-interrupted")]
    snapshot = await graph.aget_state({"configurable": {"thread_id": f.task["thread_id"]}})
    pending = f.env.service._interrupts(snapshot)
    assert len(pending) == 1
    return pending[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", [asyncio.CancelledError, GraphDrained])
async def test_retryable_interruption_preserves_checkpointed_browser_handoff(browser_task, monkeypatch, interruption):
    f = browser_task
    pending = await interrupt_browser_job(f, monkeypatch, interruption)
    assert f.task["thread_id"] not in f.browser.sessions
    handoff = await f.browser.repository.pending_handoff(f.task["thread_id"], include_expired=True)
    assert handoff["handoff_id"] == pending["value"]["handoff_id"]

    job = await f.env.repository.claim(f.task["run_id"], "after-restart")
    assert job is not None
    _ = [event async for event in f.env.service._job_stream(job, "browser-recovered")]
    waiting = await f.env.service.get("api_parent", f.task["task_id"])
    assert waiting["status"] == "waiting"
    assert waiting["interrupts"] == [pending]
    requests = await f.env.repository.browser_handoffs("api_parent", "admin:1")
    assert requests[0]["handoff_id"] == handoff["handoff_id"]
    status = await f.browser.status(f.task["thread_id"], "admin:1")
    assert status["handoff"]["handoff_id"] == handoff["handoff_id"]
    assert status["handoff_expired"] is True

    await f.env.service.resume("api_parent", f.task["task_id"], f.task["run_id"], pending["id"],
        {"action": "browser_handoff", "handoff_id": handoff["handoff_id"], "decision": "return"},
        browser_owner_id="admin:1")
    resumed = await f.env.repository.claim(f.task["run_id"], "worker")
    _ = [event async for event in f.env.service._job_stream(resumed, "browser-resumed")]
    current = await f.env.service.get("api_parent", f.task["task_id"])
    assert current["status"] == "completed"
    assert json.loads(current["result"])["handoff_status"] == "session_lost"
    assert await f.env.repository.browser_handoffs("api_parent", "admin:1") == []
    assert f.browser.sessions["api_parent"] is f.parent
    assert not f.parent.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption, expected_status", [(RuntimeError, "failed"), (TimeoutError, "timed_out")])
async def test_terminal_failure_releases_checkpointed_browser_handoff(browser_task, monkeypatch, interruption, expected_status):
    f = browser_task
    await interrupt_browser_job(f, monkeypatch, interruption)
    assert (await f.env.service.get("api_parent", f.task["task_id"]))["status"] == expected_status
    assert f.task["thread_id"] not in f.browser.sessions
    assert await f.browser.repository.pending_handoff(f.task["thread_id"], include_expired=True) is None
    assert await f.env.repository.browser_handoffs("api_parent", "admin:1") == []
    assert f.browser.sessions["api_parent"] is f.parent


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["returned", "cancelled", "expired", "session_lost"])
async def test_async_browser_handoff_resumes_its_exact_child_and_preserves_parent(waiting_browser, outcome, monkeypatch):
    f = waiting_browser
    child = f.browser.sessions[f.task["thread_id"]]
    child.driver.facade.snapshot_text = "heading Signed in [1-2]"
    if outcome == "expired":
        import agent.modules.browser.repository as browser_repository

        deadline = f.pending["value"]["expires_at"]
        monkeypatch.setattr(browser_repository.time, "time", lambda: deadline + 1)
        await f.browser.reap_idle()
    elif outcome == "session_lost":
        await f.browser.close(f.task["thread_id"], "admin:1")
    requests = await f.env.repository.browser_handoffs("api_parent", "admin:1")
    assert len(requests) == 1
    assert requests[0]["task_id"] == f.task["task_id"]
    assert requests[0]["run_id"] == f.task["run_id"]
    assert requests[0]["interrupt_id"] == f.pending["id"]
    assert await f.env.repository.browser_handoffs("another_parent", "admin:1") == []
    assert await f.env.repository.browser_handoffs("api_parent", "admin:2") == []
    decision = "cancel" if outcome == "cancelled" else "return"
    await f.env.service.resume("api_parent", f.task["task_id"], f.task["run_id"], f.pending["id"],
        {"action": "browser_handoff", "handoff_id": f.pending["value"]["handoff_id"], "decision": decision},
        browser_owner_id="admin:1")
    job = await f.env.repository.claim(f.task["run_id"], "worker")
    _ = [event async for event in f.env.service._job_stream(job, "browser-resumed")]
    current = await f.env.service.get("api_parent", f.task["task_id"])
    result = json.loads(current["result"])
    assert current["status"] == "completed"
    assert result["handoff_status"] == outcome
    if outcome == "returned":
        assert result["snapshot_status"] == "captured"
        assert "Signed in" in str(result["snapshot"])
    assert await f.env.repository.browser_handoffs("api_parent", "admin:1") == []
    assert await f.browser.repository.pending_handoff(f.task["thread_id"], include_expired=True) is None
    assert (f.task["thread_id"] in f.browser.sessions) is (outcome == "returned")
    assert f.browser.sessions["api_parent"] is f.parent
    assert not f.parent.closed
    assert f.parent.handoff_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field, value", [
    ("run_id", "another-run"), ("interrupt_id", "another-interrupt"), ("handoff_id", "another-handoff"),
    ("action", "answer"),
])
async def test_async_browser_rejects_stale_or_incompatible_responses(waiting_browser, field, value):
    f = waiting_browser
    response = {"run_id": f.task["run_id"], "interrupt_id": f.pending["id"],
        "handoff_id": f.pending["value"]["handoff_id"], "action": "browser_handoff"}
    response[field] = value
    payload = ({"action": "answer", "answers": [], "summary": "Wrong response"} if response["action"] == "answer"
               else {"action": "browser_handoff", "handoff_id": response["handoff_id"], "decision": "return"})
    with pytest.raises(ValueError):
        await f.env.service.resume("api_parent", f.task["task_id"], response["run_id"], response["interrupt_id"], payload,
                                   browser_owner_id="admin:1")
    assert (await f.env.service.get("api_parent", f.task["task_id"]))["status"] == "waiting"
    assert f.browser.sessions[f.task["thread_id"]].handoff_id == f.pending["value"]["handoff_id"]


@pytest.mark.asyncio
async def test_cancelling_waiting_async_task_releases_its_browser_and_handoff(waiting_browser):
    f = waiting_browser
    cancelled = await f.env.service.cancel("api_parent", f.task["task_id"])
    assert cancelled["status"] == "cancelled"
    assert f.task["thread_id"] not in f.browser.sessions
    assert await f.browser.repository.pending_handoff(f.task["thread_id"], include_expired=True) is None
    assert await f.env.repository.browser_handoffs("api_parent", "admin:1") == []
    assert f.browser.sessions["api_parent"] is f.parent


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_id", [None, "admin:2"])
async def test_async_browser_requires_its_authenticated_owner_to_resume(waiting_browser, owner_id):
    f = waiting_browser
    with pytest.raises(PermissionError, match="another authenticated account"):
        await f.env.service.resume("api_parent", f.task["task_id"], f.task["run_id"], f.pending["id"],
            {"action": "browser_handoff", "handoff_id": f.pending["value"]["handoff_id"], "decision": "return"},
            browser_owner_id=owner_id)
    assert (await f.env.service.get("api_parent", f.task["task_id"]))["status"] == "waiting"


@pytest.mark.asyncio
async def test_async_browser_http_resume_is_scoped_validated_and_idempotent(waiting_browser):
    import httpx
    from fastapi import FastAPI
    from agent.delivery.http.api.events_v2 import router
    from agent.modules.admin_auth import get_current_admin

    f = waiting_browser
    app = FastAPI()
    app.include_router(router)
    current_admin = ["1"]
    app.dependency_overrides[get_current_admin] = lambda: current_admin[0]
    endpoint = f"/v2/api/chat/api_parent/async-tasks/{f.task['task_id']}/resume"
    payload = {"run_id": f.task["run_id"], "interrupt_id": f.pending["id"],
        "payload": {"action": "browser_handoff", "handoff_id": f.pending["value"]["handoff_id"], "decision": "return"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(endpoint.replace("api_parent", "another_parent"), json=payload)).status_code == 404
        invalid = {**payload, "payload": {**payload["payload"], "handoff_id": "another-handoff"}}
        assert (await client.post(endpoint, json=invalid)).status_code == 409
        current_admin[0] = "2"
        assert (await client.post(endpoint, json=payload)).status_code == 403
        current_admin[0] = "1"
        assert (await client.post(endpoint, json=payload)).status_code == 202
        assert (await client.post(endpoint, json=payload)).status_code == 202
    assert (await f.env.service.get("api_parent", f.task["task_id"]))["status"] == "pending"
    assert f.browser.sessions["api_parent"] is f.parent


@pytest.mark.parametrize("thread_id, task_id", [
    ("api_parent:sub:worker:1", None), ("api_parent:sub:task", "other-task"), ("sub_worker_1", "task"),
])
def test_only_matching_async_task_metadata_enables_child_browser_handoff(thread_id, task_id):
    from agent.modules.browser.service import BrowserError, BrowserService

    with pytest.raises(BrowserError, match="synchronous delegated sub-agents"):
        BrowserService.assert_handoff_supported(thread_id, async_task_id=task_id)
