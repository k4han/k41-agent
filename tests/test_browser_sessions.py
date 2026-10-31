import asyncio
import base64
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command
from sqlalchemy import MetaData

from agent.modules.browser.models import BrowserArtifact, BrowserBinding, BrowserHandoffResume, BrowserInvocation, BrowserProfile
from agent.modules.browser.service import BrowserError, BrowserService
from agent.modules.tools.builtin.browser import browser_batch_error
from agent.shared.infrastructure.db.engine import initialize_async_engine, close_async_engine


class FakeFacade:
    def __init__(self):
        self.submits = 0
        self.failure = False

    async def run(self, **values):
        self.submits += 1
        if self.failure:
            raise ConnectionError("Lost response after submit")
        return {"submitted": self.submits}

    async def snapshot(self, **values):
        return "button Login [1-1]"

    async def screenshot(self, **values):
        return {"data": base64.b64encode(b"image").decode(), "mimeType": "image/jpeg"}


class FakeDriver:
    def __init__(self):
        self.facade = FakeFacade()
        self.closed = False

    async def close(self):
        self.closed = True

    async def tabs(self):
        if self.closed:
            raise ConnectionError("closed")
        return [{"id": "page", "url": "http://localhost", "title": "Login"}]


def make_browser_metadata():
    metadata = MetaData()
    for model in (BrowserArtifact, BrowserBinding, BrowserInvocation, BrowserProfile):
        model.__table__.to_metadata(metadata)
    return metadata


@pytest_asyncio.fixture
async def browser(isolated_container, tmp_path):
    await initialize_async_engine(metadata=make_browser_metadata(), container=isolated_container)

    async def launch(*args):
        return FakeDriver()

    service = BrowserService(isolated_container, root=tmp_path / "browser", launcher=launch)
    isolated_container._browser_service = service
    yield service
    await service.shutdown()
    await close_async_engine(container=isolated_container)


@pytest.mark.asyncio
async def test_isolation_profile_leases_and_ownership(browser):
    first = await browser.ensure("first", "admin:1")
    second = await browser.ensure("second", "admin:1")
    assert first.profile_key != second.profile_key
    with pytest.raises(PermissionError):
        await browser.ensure("first", "admin:2")
    profile = await browser.repository.create_profile("admin:1", "Personal")
    await browser.close("first", "admin:1")
    await browser.close("second", "admin:1")
    for thread in ["first", "second"]:
        await browser.select_profile(thread, "admin:1", profile["profile_id"])
    active = await browser.ensure("first", "admin:1")
    with pytest.raises(BrowserError, match="another conversation"):
        await browser.ensure("second", "admin:1")
    with pytest.raises(BrowserError):
        await browser.delete_profile("admin:1", profile["profile_id"])
    await browser.close("first", "admin:1")
    assert (await browser.ensure("second", "admin:1")).profile_key == active.profile_key


@pytest.mark.asyncio
async def test_deleting_conversation_releases_parent_and_child_browsers(browser, monkeypatch):
    from unittest.mock import AsyncMock

    from agent.modules.workflows import delete_workflow_thread_tree

    profile = await browser.repository.create_profile("admin:1", "Personal")
    await browser.select_profile("chat", "admin:1", profile["profile_id"])
    parent = await browser.ensure("chat", "admin:1")
    child = await browser.ensure("chat:sub:helper:1", "admin:1")
    child_profile_path = browser.root / "profiles" / child.profile_key
    child_artifact_path = browser.root / "run-artifacts" / browser.storage_key(child.thread_id)
    parent_artifact_path = browser.root / "run-artifacts" / browser.storage_key(parent.thread_id)
    images = []
    for thread_id in ("chat", "chat:sub:helper:1"):
        result = await browser.execute(thread_id, "admin:1", "capture", "browser_screenshot", {})
        images.append(result["artifact"]["artifact_id"])
        await browser.execute(thread_id, "admin:1", "run", "browser_run", {"code": "submit()"})
    unrelated = await browser.ensure("chat-other", "admin:1")
    lookalike = await browser.ensure("chat:submarine", "admin:1")
    profile_path = browser.root / "profiles" / parent.profile_key
    login_state = profile_path / "login-state"
    login_state.write_text("saved login", encoding="utf-8")
    parent_ticket = await browser.issue_ticket("chat", "admin:1")
    child_ticket = await browser.issue_ticket("chat:sub:helper:1", "admin:1")
    unrelated_ticket = await browser.issue_ticket("chat-other", "admin:1")
    checkpointer = InMemorySaver()
    monkeypatch.setattr("agent.modules.workflows.checkpoint.store.get_checkpointer", lambda: checkpointer)
    monkeypatch.setattr("agent.modules.tools.clear_conversation_storage", AsyncMock())

    await delete_workflow_thread_tree("chat")

    assert parent.driver.closed and child.driver.closed
    assert not unrelated.driver.closed and not lookalike.driver.closed
    assert "chat" not in browser.sessions and "chat:sub:helper:1" not in browser.sessions
    for ticket in (parent_ticket, child_ticket):
        with pytest.raises(PermissionError):
            browser.consume_ticket(ticket)
    assert browser.consume_ticket(unrelated_ticket) is unrelated
    assert await browser.repository.profile(profile["profile_id"], "admin:1") == profile
    assert login_state.read_text(encoding="utf-8") == "saved login"
    assert not child_profile_path.exists()
    assert not child_artifact_path.exists() and not parent_artifact_path.exists()
    assert not await browser.repository.thread_ids("chat")
    for artifact_id in images:
        assert await browser.repository.artifact("admin:1", artifact_id) is None
        assert not (browser.root / "images" / artifact_id).exists()
    assert await browser.repository.binding("chat-other")
    assert await browser.repository.binding("chat:submarine")
    await browser.select_profile("replacement", "admin:1", profile["profile_id"])
    assert (await browser.ensure("replacement", "admin:1")).profile_key == parent.profile_key


@pytest.mark.asyncio
async def test_deletion_cleans_persisted_data_without_existing_service(browser):
    from agent.modules.browser import delete_thread_browsers

    thread = "persisted_%"
    unrelated = "persisted_ab:sub:helper:1"
    child = f"{thread}:sub:helper:1"
    first = await browser.ensure(thread, "admin:1")
    second = await browser.ensure(child, "admin:1")
    await browser.ensure(unrelated, "admin:1")
    images = []
    for target in (thread, child, unrelated):
        result = await browser.execute(target, "admin:1", "capture", "browser_screenshot", {})
        images.append(result["artifact"]["artifact_id"])
    await browser.repository.record(child, "handoff", "hash", "handoff", {
        "handoff_id": "pending", "generation": second.generation, "expires_at": 0})
    await browser.shutdown()
    browser.container._browser_service = None
    restarted = browser.container.browser_service
    restarted.root = browser.root
    try:
        await delete_thread_browsers(thread)
        await delete_thread_browsers(thread)
        assert not await restarted.repository.thread_ids(thread)
        assert await restarted.repository.binding(unrelated)
        for current in (first, second):
            assert not (browser.root / "profiles" / current.profile_key).exists()
            assert not (browser.root / "run-artifacts" / browser.storage_key(current.thread_id)).exists()
        assert not (browser.root / "images" / images[0]).exists()
        assert not (browser.root / "images" / images[1]).exists()
        assert (browser.root / "images" / images[2]).exists()
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_subagent_owner_allows_management_but_handoff_fails_without_interrupt(browser, monkeypatch):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.result import ToolError

    thread_id = "chat:sub:helper:1"
    runtime = SimpleNamespace(context={"browser_owner_id": "admin:1"},
                              config={"configurable": {"thread_id": thread_id}}, tool_call_id="handoff")

    def unexpected(*args):
        pytest.fail("Sub-agent handoff must fail before permission or graph interrupts")

    monkeypatch.setattr(tools, "authorize", unexpected)
    monkeypatch.setattr("langgraph.types.interrupt", unexpected)
    with pytest.raises(ToolError, match="parent agent"):
        await tools._handoff(runtime, "Please sign in")
    assert not browser.sessions
    assert await browser.repository.binding(thread_id) is None
    session = await browser.ensure(*tools.identity(runtime))
    assert (await browser.status(thread_id, "admin:1"))["open"]
    with pytest.raises(PermissionError):
        await browser.status(thread_id, "admin:2")
    with pytest.raises(BrowserError, match="delegated sub-agents"):
        await browser.handoff(thread_id, "admin:1", "handoff", "Please sign in")
    assert await browser.repository.pending_handoff(thread_id) is None
    assert session.handoff_id is None
    await browser.close(thread_id, "admin:1")
    assert session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("name, arguments", [
    ("_snapshot", {}),
    ("_run", {"code": "return 1"}),
    ("_screenshot", {}),
    ("_close", {}),
    ("_handoff", {"reason": "Please sign in"}),
])
async def test_browser_tools_without_authenticated_owner_do_not_bind(browser, monkeypatch, name, arguments):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.result import ToolError, ToolErrorCode

    runtime = SimpleNamespace(context={}, config={"configurable": {"thread_id": "chat"}}, tool_call_id="call")

    def unexpected(*args):
        pytest.fail("Unauthenticated browser tools must fail before permission or graph interrupts")

    monkeypatch.setattr(tools, "authorize", unexpected)
    monkeypatch.setattr("langgraph.types.interrupt", unexpected)
    for dashboard_open in (False, True):
        with pytest.raises(ToolError, match="channel or scheduler") as error:
            await getattr(tools, name)(runtime, **arguments)
        assert error.value.code == ToolErrorCode.PERMISSION_DENIED
        binding = await browser.repository.binding(browser.canonical("chat"))
        if dashboard_open:
            assert binding["owner_id"] == "admin:1"
        else:
            assert binding is None
            assert not browser.sessions
            await browser.ensure("chat", "admin:1")


@pytest.mark.asyncio
async def test_operation_scope_serializes_waiters_without_lock_internals(browser):
    class PublicLock:
        """Expose only the public lock interface to exercise lock lifecycle handling."""

        def __init__(self):
            self.lock = asyncio.Lock()

        async def __aenter__(self):
            await self.lock.acquire()

        async def __aexit__(self, *args):
            self.lock.release()

        def locked(self):
            return self.lock.locked()

    browser.operation_locks["shared"] = PublicLock()
    entered = [asyncio.Event() for _ in range(4)]
    release = [asyncio.Event() for _ in range(4)]
    order = []
    tasks = []

    async def hold(index):
        async with browser.operation_scope("shared"):
            order.append(index)
            entered[index].set()
            await release[index].wait()

    try:
        tasks.append(asyncio.create_task(hold(0)))
        await asyncio.wait_for(entered[0].wait(), 2)
        tasks.extend(asyncio.create_task(hold(index)) for index in (1, 2))
        await asyncio.sleep(0)
        tasks[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        release[0].set()
        await tasks[0]
        await asyncio.wait_for(entered[2].wait(), 2)
        tasks.append(asyncio.create_task(hold(3)))
        await asyncio.sleep(0)
        assert not entered[3].is_set(), "New arrivals must share the lock already used by a waiter"
        release[2].set()
        await asyncio.wait_for(entered[3].wait(), 2)
        assert order == [0, 2, 3]
    finally:
        for event in release:
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not browser.operation_locks
    assert not browser.operation_users


@pytest.mark.asyncio
async def test_slow_launch_does_not_block_unrelated_tools_or_duplicate_profiles(browser):
    ready, release = asyncio.Event(), asyncio.Event()
    profile = await browser.repository.create_profile("admin:1", "Shared")
    for thread in ("slow", "same-profile"):
        await browser.select_profile(thread, "admin:1", profile["profile_id"])
    unrelated = await browser.ensure("other", "admin:1")
    launches = 0

    async def launch(*args):
        nonlocal launches
        launches += 1
        ready.set()
        await release.wait()
        return FakeDriver()

    browser.launcher = launch
    pending = asyncio.create_task(browser.ensure("slow", "admin:1"))
    duplicate = None
    try:
        await asyncio.wait_for(ready.wait(), 2)
        duplicate = asyncio.create_task(browser.ensure("same-profile", "admin:1"))
        result = await asyncio.wait_for(browser.execute("other", "admin:1", "snapshot", "browser_snapshot", {}), 2)
        assert "Login" in result["content"] and not unrelated.closed
        assert launches == 1
    finally:
        release.set()
        await pending
        if duplicate:
            with pytest.raises(BrowserError, match="another conversation"):
                await duplicate
    assert launches == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["close", "delete", "reap"])
async def test_slow_cleanup_does_not_block_unrelated_browser_tools(browser, operation):
    targets = [await browser.ensure(thread, "admin:1") for thread in ("chat", "chat:sub:helper:1", "chat:sub:helper:2")]
    await browser.ensure("other", "admin:1")
    started, release = asyncio.Event(), asyncio.Event()
    closing = 0

    async def slow_close():
        nonlocal closing
        closing += 1
        if closing == (1 if operation == "close" else 3):
            started.set()
        await release.wait()

    for current in targets:
        current.driver.close = slow_close
        current.last_activity -= browser.idle_ttl + 1
    if operation == "close":
        task = asyncio.create_task(browser.close("chat", "admin:1"))
    elif operation == "delete":
        task = asyncio.create_task(browser.delete_thread("chat"))
    else:
        task = asyncio.create_task(browser.reap_idle())
    try:
        await asyncio.wait_for(started.wait(), 2)
        result = await asyncio.wait_for(browser.execute("other", "admin:1", "snapshot", "browser_snapshot", {}), 2)
        assert "Login" in result["content"]
        if operation == "delete":
            with pytest.raises(BrowserError, match="being deleted"):
                await browser.ensure("chat:sub:new:1", "admin:1")
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_deletion_waits_for_inflight_mutation_before_removing_receipts(browser, monkeypatch):
    current = await browser.ensure("chat", "admin:1")
    started, release, deleting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_thread_ids = browser.repository.thread_ids

    async def slow_run(**values):
        started.set()
        await release.wait()
        return "submitted"

    async def list_threads(thread_id):
        result = await original_thread_ids(thread_id)
        deleting.set()
        return result

    current.driver.facade.run = slow_run
    monkeypatch.setattr(browser.repository, "thread_ids", list_threads)
    mutation = asyncio.create_task(browser.execute("chat", "admin:1", "submit", "browser_run", {"code": "submit()"}))
    cleanup = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        cleanup = asyncio.create_task(browser.delete_thread("chat"))
        await asyncio.wait_for(deleting.wait(), 2)
        assert not cleanup.done()
    finally:
        release.set()
        await mutation
        if cleanup:
            await cleanup
    assert current.closed
    assert not await original_thread_ids("chat")
    assert await browser.repository.invocation("chat", "submit") is None


@pytest.mark.asyncio
async def test_mutation_replay_is_cached_and_unknown_outcomes_are_not_retried(browser):
    session = await browser.ensure("chat", "admin:1")
    args = {"code": "submit()"}
    results = await asyncio.gather(*(browser.execute("chat", "admin:1", "call", "browser_run", args) for _ in range(2)))
    assert results[0] == results[1]
    assert session.driver.facade.submits == 1
    with pytest.raises(PermissionError):
        await browser.execute("chat", "admin:2", "call", "browser_run", args)
    session.driver.facade.failure = True
    with pytest.raises(BrowserError, match="partially executed"):
        await browser.execute("chat", "admin:1", "failed", "browser_run", args)
    with pytest.raises(BrowserError, match="already have executed"):
        await browser.execute("chat", "admin:1", "failed", "browser_run", args)
    assert session.driver.facade.submits == 2
    with pytest.raises(BrowserError, match="changed"):
        await browser.execute("chat", "admin:1", "call", "browser_run", {"code": "other()"})


def handoff_graph(browser):
    async def node(state):
        await browser.execute("chat", "admin:1", "submit", "browser_run", {"code": "submit()"})
        return {"result": await browser.handoff("chat", "admin:1", "handoff", "Please sign in")}

    graph = StateGraph(dict)
    graph.add_node("browser", node)
    graph.add_edge(START, "browser")
    graph.add_edge("browser", END)
    return graph.compile(checkpointer=InMemorySaver())


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_id", [None, "older-checkpoint"])
@pytest.mark.parametrize("admin", ["1", "2"])
async def test_history_returns_live_handoff_independently_of_checkpoint(browser, monkeypatch, checkpoint_id, admin):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.delivery.http.dashboard.routes import conversations
    from agent.modules.admin_auth import get_current_admin

    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    pending = await browser.repository.pending_handoff(browser.canonical("chat"))
    monkeypatch.setattr(browser.container, "_persistence_ready", True)
    messages = AsyncMock(return_value=([], checkpoint_id or "current-checkpoint"))
    monkeypatch.setattr(conversations, "get_thread_messages_payload", messages)
    monkeypatch.setattr(conversations, "get_conversation_thread", AsyncMock(return_value={"agent_name": "default"}))
    monkeypatch.setattr(conversations, "workspace_ref_for_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(conversations, "get_thread_resume_error", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.modules.tools.pending_permission_requests", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(conversations.router)
    app.dependency_overrides[get_current_admin] = lambda: admin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard-api/chat-history/chat", params={"checkpoint_id": checkpoint_id} if checkpoint_id else {})
    assert response.status_code == 200
    assert response.json().get("pending_browser_handoffs", []) == ([pending] if admin == "1" else [])
    messages.assert_awaited_once_with(browser.canonical("chat"), checkpoint_id=checkpoint_id, include_branch_metadata=True)
    assert (await graph.aget_state(config)).tasks[0].interrupts
    resumed = await graph.ainvoke(Command(resume={
        "action": "browser_handoff", "handoff_id": pending["handoff_id"], "decision": "return",
    }), config)
    assert "returned" in resumed["result"]["content"].lower()
    assert await browser.repository.pending_handoff(browser.canonical("chat")) is None


@pytest.mark.asyncio
async def test_handoff_interrupt_replay_and_reconnect(browser):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    session = next(iter(browser.sessions.values()))
    status = await browser.status("chat", "admin:1")
    assert status["mode"] == "human"
    with pytest.raises(BrowserError, match="user controls"):
        await browser.execute("chat", "admin:1", "blocked", "browser_snapshot", {})
    ticket = await browser.issue_ticket("chat", "admin:1")
    assert browser.consume_ticket(ticket) is session
    with pytest.raises(PermissionError):
        browser.consume_ticket(ticket)
    assert (await browser.status("chat", "admin:1"))["mode"] == "human"
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": session.handoff_id,
                                                "decision": "return"}), config)
    assert "fresh" in result["result"]["content"]
    assert session.driver.facade.submits == 1
    assert session.handoff_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("decision, expected", [("return", "lost"), ("cancel", "cancelled")])
async def test_handoff_survives_browser_loss_without_accepting_stale_control(browser, decision, expected):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    pending = (await browser.status("chat", "admin:1"))["handoff"]
    await browser.close("chat", "admin:1")
    assert (await browser.status("chat", "admin:1"))["handoff_expired"]
    with pytest.raises(BrowserError, match="expired handoff"):
        await browser.ensure("chat", "admin:1")
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": pending["handoff_id"],
                                                "decision": decision}), config)
    assert expected in result["result"]["content"]
    assert not browser.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["status", "ensure", "reap"])
async def test_expired_handoff_releases_control_and_remains_safe_to_replay(browser, cleanup):
    graph = handoff_graph(browser)
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    current = browser.sessions["chat"]
    prior = await browser.repository.invocation("chat", "handoff")
    payload = await browser.repository.pending_handoff("chat")
    assert payload["expires_at"] > 0
    payload["expires_at"] = 0
    await browser.repository.record("chat", "handoff", prior.arguments_hash, "handoff", payload)
    if cleanup == "status":
        assert (await browser.status("chat", "admin:1"))["handoff"] is None
    elif cleanup == "ensure":
        assert await browser.ensure("chat", "admin:1") is not current
    else:
        await browser.reap_idle()
    assert current.closed
    assert await browser.repository.pending_handoff("chat") is None
    assert (await browser.repository.invocation("chat", "handoff")).status == "completed"
    replacement = await browser.ensure("chat", "admin:1")
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": payload["handoff_id"],
                                               "decision": "return"}), config)
    assert "expired" in result["result"]["content"]
    assert not replacement.closed
    assert replacement.handoff_id is None
    assert current.driver.facade.submits == 1


@pytest.mark.asyncio
async def test_legacy_handoff_without_expiry_does_not_permanently_block_thread(browser):
    await browser.bind("chat", "admin:1")
    await browser.repository.record("chat", "legacy", "hash", "handoff", {
        "handoff_id": "orphan", "generation": "old-process", "thread_id": "chat"})
    assert await browser.ensure("chat", "admin:1")
    assert await browser.repository.pending_handoff("chat") is None
    assert (await browser.repository.invocation("chat", "legacy")).status == "completed"


@pytest.mark.asyncio
async def test_screenshot_owner_and_profile_survive_closing(browser):
    await browser.ensure("chat", "admin:1")
    result = await browser.execute("chat", "admin:1", "capture", "browser_screenshot", {})
    artifact_id = result["artifact"]["artifact_id"]
    assert await browser.repository.artifact("admin:1", artifact_id)
    assert await browser.repository.artifact("admin:2", artifact_id) is None
    directory = next(iter(browser.sessions.values())).profile_key
    await browser.close("chat", "admin:1")
    assert (browser.root / "profiles" / directory).is_dir()


def test_batch_rejection_and_resume_schema():
    assert browser_batch_error([{"name": "browser_run"}, {"name": "browser_snapshot"}])
    assert browser_batch_error([{"name": "browser_handoff"}, {"name": "bash"}])
    assert browser_batch_error([{"name": "browser_run"}, {"name": "read"}]) is None
    assert BrowserHandoffResume(action="browser_handoff", handoff_id="id", decision="return")
    from langchain_core.messages import AIMessage
    from agent.modules.workflows.nodes.tool import _include_pending_control_tools
    state = {"messages": [AIMessage(content="", tool_calls=[{
        "id": "uncatalogued", "name": "browser_handoff", "args": {"reason": "Sign in"}}])]}
    assert _include_pending_control_tools(state, []) == []


def test_batch_guard_uses_browser_registration(monkeypatch):
    from agent.modules.tools.builtin import browser as tools

    async def extra():
        return "snapshot"

    monkeypatch.setattr(tools, "BROWSER_TOOLS", dict(tools.BROWSER_TOOLS))
    tools.registered("browser_new_operation", extra, "Read another browser snapshot.")
    assert browser_batch_error([{"name": "browser_new_operation"}, {"name": "browser_snapshot"}])
    assert browser_batch_error([{"name": "browser_new_operation"}, {"name": "read"}]) is None


@pytest.mark.asyncio
async def test_tool_node_injects_trusted_owner_and_keeps_model_images(browser, monkeypatch):
    from langchain_core.messages import AIMessage
    from langgraph.prebuilt import ToolNode
    from langgraph.runtime import Runtime
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.workflows.run_config import WorkflowContext

    authorized = []
    monkeypatch.setattr(tools, "authorize", lambda runtime, name, values: authorized.append(name))
    runtime = Runtime(context=WorkflowContext(browser_owner_id="admin:1"))
    config = {"configurable": {"thread_id": "chat"}}
    node = ToolNode([tools.browser_run, tools.browser_screenshot])
    result = await node.ainvoke({"messages": [AIMessage(content="", tool_calls=[{
        "id": "call", "name": "browser_run", "args": {"code": "submit()"}}])]}, config=config, runtime=runtime)
    assert result["messages"][0].status == "success"
    assert next(iter(browser.sessions.values())).owner_id == "admin:1"
    result = await node.ainvoke({"messages": [AIMessage(content="", tool_calls=[{
        "id": "image", "name": "browser_screenshot", "args": {}}])]}, config=config, runtime=runtime)
    message = result["messages"][0]
    assert message.content[1]["type"] == "image"
    assert message.artifact["browser"]["image_url"].startswith("/dashboard-api/browser/artifacts/")
    assert authorized == ["browser_run", "browser_screenshot"]
    assert "runtime" not in tools.browser_run.tool_call_schema.model_json_schema()["properties"]


@pytest.mark.asyncio
async def test_ticket_expiry_and_status_do_not_create_browsers(browser):
    import time

    assert not (await browser.status("empty", "admin:1"))["open"]
    assert await browser.repository.binding(browser.canonical("empty")) is None
    with pytest.raises(BrowserError):
        await browser.issue_ticket("empty", "admin:1")
    current = await browser.ensure("chat", "admin:1")
    ticket = await browser.issue_ticket("chat", "admin:1")
    browser.tickets[ticket] = (current.thread_id, current.owner_id, current.generation, time.monotonic() - 1)
    with pytest.raises(PermissionError):
        browser.consume_ticket(ticket)
    await browser.close("chat", "admin:1")
    with pytest.raises(BrowserError):
        await browser.issue_ticket("chat", "admin:1")
    assert not browser.sessions


@pytest.mark.asyncio
async def test_timeout_closes_browser_and_never_repeats_uncertain_mutation(browser, monkeypatch):
    session = await browser.ensure("chat", "admin:1")
    monkeypatch.setattr(BrowserService, "timeout", property(lambda self: 0.02))

    async def stalled(**values):
        await asyncio.sleep(1)

    session.driver.facade.run = stalled
    with pytest.raises(TimeoutError):
        await browser.execute("chat", "admin:1", "timeout", "browser_run", {"code": "submit()"})
    assert session.driver.closed and not browser.sessions
    with pytest.raises(BrowserError, match="already have executed"):
        await browser.execute("chat", "admin:1", "timeout", "browser_run", {"code": "submit()"})


@pytest.mark.asyncio
async def test_idle_cleanup_and_close_replay_preserve_profiles(browser):
    session = await browser.ensure("chat", "admin:1")
    profile = session.profile_key
    session.last_activity -= browser.idle_ttl + 1
    await browser.reap_idle()
    assert session.driver.closed and not browser.sessions
    assert (browser.root / "profiles" / profile).exists()
    await browser.execute("chat", "admin:1", "close", "browser_close", {})
    reopened = await browser.ensure("chat", "admin:1")
    await browser.execute("chat", "admin:1", "close", "browser_close", {})
    assert not reopened.closed


@pytest.mark.asyncio
async def test_handoff_consumes_old_permission_resume_without_releasing_control(browser):
    from langgraph.types import interrupt

    permitted = False

    async def node(state):
        nonlocal permitted
        if not permitted:
            interrupt({"type": "permission_request"})
            permitted = True
        return {"result": await browser.handoff("chat", "admin:1", "handoff", "Please sign in")}

    builder = StateGraph(dict)
    builder.add_node("browser", node)
    builder.add_edge(START, "browser")
    builder.add_edge("browser", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "graph"}}
    await graph.ainvoke({}, config)
    await graph.ainvoke(Command(resume={"action": "permission", "decision": "allow_once"}), config)
    pending = await browser.repository.owned_handoff(browser.canonical("chat"), "admin:1")
    assert pending and await browser.repository.owned_handoff(browser.canonical("chat"), "admin:2") is None
    result = await graph.ainvoke(Command(resume={"action": "browser_handoff", "handoff_id": pending["handoff_id"], "decision": "cancel"}), config)
    assert "cancelled" in result["result"]["content"]
    assert not browser.sessions


def test_browser_permission_rules_are_enforced(tmp_path, monkeypatch):
    from agent.modules.tools.builtin import browser as tools
    from agent.modules.tools.coding.contracts import InvocationContext
    from agent.modules.tools.coding.models import PermissionRule
    from agent.modules.tools.coding.permissions import Permissions
    from agent.modules.tools.coding.storage import OutputStore
    from agent.modules.tools.result import ToolError

    context = InvocationContext(agent_name="test", workspace=str(tmp_path), thread_id="chat",
        permission_rules=(PermissionRule(action="browser_run", resource="*", effect="deny"),))
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)
    monkeypatch.setattr("agent.modules.tools.coding.service.get_coding_service",
                        lambda: SimpleNamespace(permissions=Permissions(OutputStore(tmp_path))))
    with pytest.raises(ToolError, match="Policy denies browser_run"):
        tools.authorize(SimpleNamespace(), "browser_run", {"code": "submit()"})
