"""Shared browser retention, resource admission, and human wait deadlines."""

import asyncio
import time

import pytest

from agent.modules.browser import browser_run_lifecycle
from agent.modules.browser.service import BrowserError
from test_async_subagents import admit, repository, runtime_environment
from test_browser_sessions import browser


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "timed_out"])
async def test_finished_runs_get_a_full_retention_window(browser, outcome):
    current = await browser.ensure("child:sub:work", "admin:1")
    current.last_activity -= browser.idle_ttl + 1
    await browser.finish_run(current.thread_id, outcome)
    await browser.reap_idle()
    status = await browser.status(current.thread_id, "admin:1")
    assert status["open"] and status["state"] == "idle"
    assert status["expires_at"] > time.time() + browser.idle_ttl - 2
    assert status["source"] == "subagent"
    current.last_activity -= browser.idle_ttl + 1
    await browser.reap_idle()
    assert current.closed and current.driver.closed


@pytest.mark.asyncio
async def test_capacity_evicts_oldest_idle_without_touching_active_or_human(browser, monkeypatch):
    read = browser.container.config_service.get_int
    monkeypatch.setattr(browser.container.config_service, "get_int", lambda key, default=None:
        3 if key == "browser.max_sessions_per_owner" else read(key, default))
    active = await browser.ensure("active", "admin:1")
    idle = await browser.ensure("idle", "admin:1")
    human = await browser.ensure("human", "admin:1")
    human.handoff_id = "pending"
    human.handoff_expires_at = time.time() + 1800
    active.last_activity -= 1000
    async with active.lock:
        fresh = await browser.ensure("new", "admin:1")
        assert idle.closed and not active.closed and not human.closed and not fresh.closed
        fresh.handoff_id = "other"
        fresh.handoff_expires_at = time.time() + 1800
        with pytest.raises(BrowserError, match="capacity is full"):
            await browser.ensure("overflow", "admin:1")
        assert not browser.reservations


@pytest.mark.asyncio
async def test_capacity_is_per_account_and_reserves_concurrent_launches(browser, monkeypatch):
    read = browser.container.config_service.get_int
    monkeypatch.setattr(browser.container.config_service, "get_int", lambda key, default=None:
        1 if key == "browser.max_sessions_per_owner" else read(key, default))
    entered, release = asyncio.Event(), asyncio.Event()
    launch = browser.launcher

    async def slow(*args):
        entered.set()
        await release.wait()
        return await launch(*args)

    monkeypatch.setattr(browser, "launcher", slow)
    task = asyncio.create_task(browser.ensure("first", "admin:1"))
    await entered.wait()
    try:
        with pytest.raises(BrowserError, match="capacity is full"):
            await browser.ensure("second", "admin:1")
    finally:
        release.set()
        await task
    await browser.ensure("other-account", "admin:2")
    assert len(browser.sessions) == 2


@pytest.mark.asyncio
async def test_human_deadline_is_independent_of_idle_and_activity(browser):
    _, pending, _ = await browser._open_handoff("human", "admin:1", "handoff", "Sign in")
    current = browser.sessions["human"]
    assert pending["expires_at"] > time.time() + browser.handoff_ttl - 2
    current.last_activity -= browser.idle_ttl + 1
    await browser.reap_idle()
    assert not current.closed
    current.last_activity = time.monotonic()
    current.handoff_expires_at = time.time() - 1
    await browser.reap_idle()
    assert current.closed


@pytest.mark.asyncio
async def test_runtime_lifecycle_retains_success_and_closes_cancelled_runs(browser):
    @browser_run_lifecycle
    async def run(*, thread_id, cancel=False):
        await browser.ensure(thread_id, "admin:1")
        if cancel:
            raise asyncio.CancelledError
        yield "done"

    assert [item async for item in run(thread_id="chat")] == ["done"]
    assert not browser.sessions["chat"].closed
    with pytest.raises(asyncio.CancelledError):
        _ = [item async for item in run(thread_id="cancelled", cancel=True)]
    assert "cancelled" not in browser.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_parent_wakeup_gets_a_full_retention_window(runtime_environment, browser, fails):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import START, END, StateGraph
    from agent.modules.workflows.run_config import WorkflowContext
    from agent.modules.workflows.state.base import BaseState

    env = runtime_environment
    task = await admit(env.repository)
    child = await env.repository.claim(task["run_id"], "worker")
    await env.repository.finish(child, "completed", result="Done")
    await env.repository.prepare_wakeups()
    candidate = next(item for item in await env.repository.candidates() if item["kind"] == "wakeup")
    wakeup = await env.repository.claim(candidate["job_id"], "worker")
    current = await browser.ensure(wakeup["thread_id"], "admin:1")

    async def work(state):
        current.last_activity -= browser.idle_ttl + 1
        if fails:
            raise RuntimeError("Parent execution failed.")
        return {"messages": [AIMessage(content="Combined answer")]}

    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    env.current_graph[0] = builder.compile(checkpointer=InMemorySaver())
    if fails:
        with pytest.raises(RuntimeError, match="Parent execution failed"):
            _ = [event async for event in env.service._job_stream(wakeup, "parent-wakeup")]
    else:
        _ = [event async for event in env.service._job_stream(wakeup, "parent-wakeup")]

    await browser.reap_idle()
    status = await browser.status(current.thread_id, "admin:1")
    assert status["open"] and status["state"] == "idle"
    assert status["expires_at"] > time.time() + browser.idle_ttl - 2
    current.last_activity -= browser.idle_ttl + 1
    await browser.reap_idle()
    assert current.closed and current.driver.closed
