import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from agent.delivery.http.api import events_v2
from agent.modules.admin_auth import get_current_admin
from agent.modules.agent_runtime import ChatQueueConflict, runner
from agent.modules.agent_runtime.protocol import application_event
from agent.modules.agents import AgentConfig
from agent.modules.workflows.graphs import react_agent, research, router
from agent.modules.workflows.nodes import llm
from agent.modules.workflows.registry import GraphRegistry


THREAD_ID = "dashboard-queue"
OWNER = "admin:test"
QUEUE_URL = f"/v2/api/chat/{THREAD_ID}/message-queue"


class ControlledModel:
    def __init__(self):
        self.inputs = asyncio.Queue()
        self.responses = asyncio.Queue()

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, config=None):
        self.inputs.put_nowait(messages)
        result = await self.responses.get()
        if isinstance(result, Exception):
            raise result
        return result

    async def next_input(self):
        return await asyncio.wait_for(self.inputs.get(), timeout=5)

    def answer(self, text="Answered.", *, tool=False):
        calls = [{"id": "tool-1", "name": "read", "args": {}}] if tool else []
        self.responses.put_nowait(AIMessage(content=text, tool_calls=calls))


class QueueEnvironment:
    def __init__(self, container, graph, model, tool_ready, allow_tool):
        self.manager = container.chat_stream_manager
        self.graph = graph
        self.model = model
        self.tool_ready = tool_ready
        self.allow_tool = allow_tool

    async def start(self, text="Initial message", **parameters):
        return await self.manager.get_or_create_session(THREAD_ID, {
            "thread_id": THREAD_ID,
            "user_input": text,
            "browser_owner_id": OWNER,
            "usage_context": {"platform": "api", "user_id": "dashboard"},
            **parameters,
        }, queue_owner=OWNER)

    async def enqueue(self, session, message_id, text):
        return await self.manager.enqueue_message(THREAD_ID, OWNER, session.run_id, message_id, text, [])

    def snapshot(self):
        return self.manager.message_queue_snapshot(THREAD_ID, OWNER)

    async def messages(self):
        return (await self.graph.aget_state({"configurable": {"thread_id": THREAD_ID}})).values["messages"]


def user_messages(messages):
    return [message.content for message in messages if isinstance(message, HumanMessage)]


@pytest.fixture
def queue_env(monkeypatch, isolated_container):
    model = ControlledModel()
    tool_ready = asyncio.Event()
    allow_tool = asyncio.Event()

    async def use_tool(state):
        tool_ready.set()
        await allow_tool.wait()
        message = state["messages"][-1]
        if message.content == "Pause for input.":
            interrupt({"type": "test_input"})
        return {"messages": [ToolMessage(content="Tool finished.", name="read", tool_call_id="tool-1")]}

    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())
    monkeypatch.setattr(react_agent, "tool_node", use_tool)
    react_agent.build_react_graph(checkpointer=InMemorySaver())
    graph = GraphRegistry.get("react_agent")
    monkeypatch.setattr(runner, "get_workflow_graph", lambda name: graph)
    monkeypatch.setattr(runner, "_record_conversation_thread", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.modules.conversations.record_thread_run_error", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(
        get_agent=lambda name: SimpleNamespace(graph_type="react_agent", provider="test", model="test", system_prompt="Test"),
    ))
    monkeypatch.setattr(llm, "get_resolved_chat_model", lambda **kwargs: SimpleNamespace(
        model=model, provider_name="test", model_name="test",
    ))
    monkeypatch.setattr(llm.ToolResolver, "aresolve_for_agent", AsyncMock(return_value=[]))
    monkeypatch.setattr(llm, "get_runtime_prompt_variable_values", AsyncMock(return_value={}))
    monkeypatch.setattr(llm, "build_llm_system_prompt", lambda **kwargs: "Test system prompt")
    return QueueEnvironment(isolated_container, graph, model, tool_ready, allow_tool)


@pytest.mark.asyncio
async def test_queued_inputs_are_inserted_after_tool_results_and_saved_once(queue_env, monkeypatch, tmp_path):
    from agent.modules.tools.runtime import thread_storage

    env = queue_env
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    app = FastAPI()
    app.include_router(events_v2.router)
    app.dependency_overrides[get_current_admin] = lambda: "test"
    async def prepare(request, browser_owner_id):
        return {
            "thread_id": THREAD_ID, "user_input": request.message, "browser_owner_id": browser_owner_id,
            "workspace": str(workspace),
            "usage_context": {"platform": "api", "user_id": request.user_id},
        }

    monkeypatch.setattr(events_v2, "prepare_run_params", prepare)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            start_request = {
                "thread_id": THREAD_ID, "message": "Initial message", "enable_message_queue": True, "user_id": "dashboard",
            }
            request_task = asyncio.create_task(client.post("/v2/api/chat/events", json=start_request))
            assert user_messages(await env.model.next_input()) == ["Initial message"]
            session = await env.manager.get_session(THREAD_ID)
            conflict = await client.post("/v2/api/chat/events", json={**start_request, "message": "Other tab"})
            assert conflict.status_code == 409 and conflict.json()["detail"]["run_id"] == session.run_id
            env.model.answer("Running a tool.", tool=True)
            await asyncio.wait_for(env.tool_ready.wait(), timeout=5)

            async def submit(message_id, text, attachments=None):
                response = await client.post(QUEUE_URL, json={
                    "id": message_id, "run_id": session.run_id, "text": text, "attachments": attachments or [],
                })
                assert response.status_code == 202, response.text
                return response.json()

            await submit("normal", "Next turn")
            report = {"kind": "file", "name": "report.xlsx"}
            await submit("first", "Change this", [
                {"kind": "text", "name": "note.txt", "content": "Additional context"},
                {**report, "content": "First report"},
            ])
            await submit("second", "Second insertion", [{**report, "content": "Second report"}])
            await submit("removed", "Remove this")
            await submit("normal", "A repeated submit must not replace the original")

            edited = await client.patch(f"{QUEUE_URL}/first", json={"text": "First insertion", "revision": 0})
            assert edited.status_code == 200
            conflicted = await client.patch(f"{QUEUE_URL}/first", json={"text": "Stale edit", "revision": 0})
            assert conflicted.status_code == 409
            assert (await client.delete(f"{QUEUE_URL}/removed?revision=0")).status_code == 200
            results = await asyncio.gather(
                client.post(f"{QUEUE_URL}/second/send-now", json={"revision": 0}),
                client.post(f"{QUEUE_URL}/first/send-now", json={"revision": 1}),
            )
            assert all(response.status_code == 200 for response in results)
            assert (await client.get(QUEUE_URL)).json()["can_insert"]

            env.allow_tool.set()
            model_input = await env.model.next_input()
            assert isinstance(model_input[-3], ToolMessage)
            assert model_input[-2].content.startswith("First insertion")
            assert "Additional context" in model_input[-2].content
            assert model_input[-1].content.startswith("Second insertion")
            paths = [message.content.split("Workspace path: ", 1)[1].splitlines()[0] for message in model_input[-2:]]
            assert paths[0] != paths[1]
            assert [(workspace / path).read_text(encoding="utf-8") for path in paths] == ["First report", "Second report"]
            env.model.answer()

            assert user_messages(await env.model.next_input())[-1] == "Next turn"
            env.model.answer()
            response = await asyncio.wait_for(request_task, timeout=5)
            assert response.headers["X-Run-ID"] == session.run_id

        persisted = await env.messages()
        assert [message.id for message in persisted if isinstance(message, HumanMessage)][1:] == [
            "user-queued-first", "user-queued-second", "user-queued-normal",
        ]
        assert env.snapshot()["messages"] == []

        replay = [event async for event in session.subscribe()]
        root_lifecycle = [event["params"]["data"]["event"] for event in replay if event["method"] == "lifecycle" and not event["params"]["namespace"]]
        assert root_lifecycle == ["started", "completed"]
        updates = [application_event(event) or {} for event in replay]
        delivered = [(update["text"], update["inserted"]) for update in updates if update.get("type") == "chat_queue_delivery"]
        assert delivered == [("First insertion", True), ("Second insertion", True), ("Next turn", False)]
        assert next(index for index, update in enumerate(updates) if update.get("type") == "tool_result") < next(
            index for index, update in enumerate(updates) if update.get("type") == "chat_queue_delivery"
        )
    finally:
        await env.manager.shutdown()


@pytest.mark.asyncio
async def test_priority_requested_during_last_call_opens_next_turn(queue_env):
    env = queue_env
    session = await env.start()
    try:
        await env.model.next_input()
        await env.enqueue(session, "normal", "Normal turn")
        await env.enqueue(session, "priority", "Priority turn")
        await env.manager.send_queued_message_now(THREAD_ID, OWNER, "priority", 0)
        env.model.answer()
        assert user_messages(await env.model.next_input()) == ["Initial message", "Priority turn"]
        env.model.answer()
        assert user_messages(await env.model.next_input())[-1] == "Normal turn"
        env.model.answer()
        await asyncio.wait_for(session.task, timeout=5)
        assert env.snapshot()["status"] == "idle"
    finally:
        await env.manager.shutdown()


@pytest.mark.asyncio
async def test_enqueue_arriving_after_completion_starts_a_followup_once(queue_env):
    env = queue_env
    session = await env.start(permission_mode="full_access")
    try:
        await env.model.next_input()
        env.model.answer()
        await asyncio.wait_for(session.task, timeout=5)

        accepted = await env.manager.enqueue_message(
            THREAD_ID, OWNER, session.run_id, "late", "Late arrival", [], permission_mode="ask",
        )
        next_run = await env.manager.get_session(THREAD_ID)
        assert accepted["run_id"] == next_run.run_id != session.run_id
        assert next_run.params["permission_mode"] == "ask"
        assert user_messages(await env.model.next_input())[-1] == "Late arrival"
        env.model.answer()
        await asyncio.wait_for(next_run.task, timeout=5)

        assert (await env.enqueue(session, "late", "Late arrival"))["messages"] == []
        assert (await env.manager.get_session(THREAD_ID)) is next_run
        assert user_messages(await env.messages()).count("Late arrival") == 1
    finally:
        await env.manager.shutdown()


@pytest.mark.asyncio
async def test_stop_during_late_enqueue_holds_message_until_send_now(queue_env, monkeypatch):
    env = queue_env
    session = await env.start(permission_mode="full_access")
    try:
        await env.model.next_input()
        env.model.answer()
        await asyncio.wait_for(session.task, timeout=5)

        preparing = asyncio.Event()
        allow_start = asyncio.Event()

        async def prepare(thread_id, *, check_only=False):
            assert thread_id == THREAD_ID and check_only
            preparing.set()
            await allow_start.wait()

        monkeypatch.setattr(env.manager, "_prepare_new_run", prepare)
        enqueue = asyncio.create_task(env.enqueue(session, "late", "Late arrival"))
        try:
            await asyncio.wait_for(preparing.wait(), timeout=5)
            assert env.manager.pause_message_queue(THREAD_ID)
        finally:
            allow_start.set()

        snapshot = await asyncio.wait_for(enqueue, timeout=5)
        assert snapshot["status"] == "paused" and snapshot["run_id"] is None
        assert snapshot["last_run_id"] == session.run_id
        assert (await env.manager.get_session(THREAD_ID)) is session
        assert env.model.inputs.empty()

        app = FastAPI()
        app.include_router(events_v2.router)
        app.dependency_overrides[get_current_admin] = lambda: "test"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"{QUEUE_URL}/late/send-now", json={"revision": 0, "permission_mode": "ask"})
            assert response.status_code == 200, response.text

        follow_up = await env.manager.get_session(THREAD_ID)
        assert follow_up.run_id != session.run_id
        assert follow_up.params["permission_mode"] == "ask"
        assert user_messages(await env.model.next_input())[-1] == "Late arrival"
        env.model.answer()
        await asyncio.wait_for(follow_up.task, timeout=5)
        assert env.snapshot()["messages"] == []
    finally:
        await env.manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", ["research_chain", "router"])
async def test_workflow_uses_queued_inputs_at_its_next_conversation_model_call(queue_env, monkeypatch, workflow):
    env = queue_env
    if workflow == "research_chain":
        resolved = SimpleNamespace(model=env.model, provider_name="test", model_name="test")
        monkeypatch.setattr(research, "_resolve_runtime_model", lambda runtime: (resolved, {}, SimpleNamespace()))
        monkeypatch.setattr(research, "_research_system", AsyncMock(return_value=SystemMessage(content="Research")))
        monkeypatch.setattr(research, "get_checkpointer", InMemorySaver)
        research.build_research_graph()
    else:
        async def choose_target(state):
            return {"target_agent": AgentConfig(name="default", graph_type="react_agent")}

        monkeypatch.setattr(router, "llm_call_router", choose_target)
        monkeypatch.setattr(router, "get_checkpointer", InMemorySaver)
        router.build_router_graph()
    env.graph = GraphRegistry.get(workflow)
    monkeypatch.setattr(runner, "get_workflow_graph", GraphRegistry.get)

    session = await env.start(workflow=workflow)
    try:
        assert user_messages(await env.model.next_input()) == ["Initial message"]
        await env.enqueue(session, "addition", "Add this detail")
        await env.manager.send_queued_message_now(THREAD_ID, OWNER, "addition", 0)
        env.allow_tool.set()
        env.model.answer("Initial response", tool=workflow == "router")
        assert user_messages(await env.model.next_input())[-1] == "Add this detail"
        env.model.answer()
        await asyncio.wait_for(session.task, timeout=5)
        deliveries = [update async for event in session.subscribe() if (update := application_event(event))
                      and update.get("type") == "chat_queue_delivery"]
        assert [(update["text"], update["inserted"]) for update in deliveries] == [("Add this detail", True)]
        assert user_messages(await env.messages()).count("Add this detail") == 1
        assert env.snapshot()["messages"] == []
    finally:
        await env.manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stopped", [True, False])
async def test_stop_or_failed_call_restores_uncommitted_input_and_holds_queue(queue_env, stopped):
    env = queue_env
    session = await env.start()
    try:
        await env.model.next_input()
        await env.enqueue(session, "restore", "Restore this")
        await env.enqueue(session, "after", "Keep waiting")
        await env.manager.send_queued_message_now(THREAD_ID, OWNER, "restore", 0)
        env.allow_tool.set()
        env.model.answer("Running a tool.", tool=True)
        assert user_messages(await env.model.next_input())[-1] == "Restore this"
        if stopped:
            session.cancel()
        else:
            env.model.responses.put_nowait(RuntimeError("Model failure"))
        await asyncio.wait_for(asyncio.gather(session.task, return_exceptions=True), timeout=5)

        snapshot = env.snapshot()
        assert snapshot["status"] == "paused"
        assert [message["text"] for message in snapshot["messages"]] == ["Restore this", "Keep waiting"]
        assert not any(message["sending"] for message in snapshot["messages"])
        assert "Restore this" not in user_messages(await env.messages())
        assert env.model.inputs.empty()

        await env.manager.send_queued_message_now(THREAD_ID, OWNER, "restore", snapshot["messages"][0]["revision"])
        retry = await env.manager.get_session(THREAD_ID)
        assert retry.run_id != session.run_id
        assert user_messages(await env.model.next_input()).count("Restore this") == 1
        env.model.answer()
        assert user_messages(await env.model.next_input())[-1] == "Keep waiting"
        env.model.answer()
        await asyncio.wait_for(retry.task, timeout=5)
        assert env.snapshot()["messages"] == []
    finally:
        await env.manager.shutdown()


@pytest.mark.asyncio
async def test_pending_interrupt_prevents_autosend_until_the_turn_resumes(queue_env):
    env = queue_env
    session = await env.start()
    try:
        await env.model.next_input()
        await env.enqueue(session, "waiting", "After answer")
        env.allow_tool.set()
        env.model.answer("Pause for input.", tool=True)
        await asyncio.wait_for(session.task, timeout=5)
        assert env.snapshot()["status"] == "waiting"
        with pytest.raises(ChatQueueConflict, match="pending request"):
            await env.manager.send_queued_message_now(THREAD_ID, OWNER, "waiting", 0)

        resumed = await env.start("", resume_payload={"action": "answer", "summary": "Continue"})
        await env.model.next_input()
        env.model.answer("Continued.")
        assert user_messages(await env.model.next_input())[-1] == "After answer"
        env.model.answer()
        await asyncio.wait_for(resumed.task, timeout=5)
        assert env.snapshot()["messages"] == []
    finally:
        await env.manager.shutdown()
