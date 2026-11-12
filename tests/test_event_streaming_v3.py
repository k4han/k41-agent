"""Behavioral coverage for v3 protocol execution, replay, and delegated history."""

import asyncio
import importlib
import json
from contextlib import aclosing
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, ToolRuntime

from agent.modules.agent_runtime.chat_stream_manager import ChatStreamManager
from agent.modules.agent_runtime.protocol import (
    EventSequence, application_event, protocol_event, run_agent_protocol_stream, stream_graph,
    whole_message_events,
)


def compile_graph(node, *, saver=None):
    builder = StateGraph(MessagesState)
    builder.add_node("work", node)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    return builder.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_graphs_must_implement_the_v3_run_contract():
    async def astream(*args, **kwargs):
        pytest.fail("The runner must not fall back to graph astream.")
        yield

    graph = SimpleNamespace(astream=astream)
    with pytest.raises(AttributeError, match="astream_events"):
        await anext(stream_graph(graph, {}, config={}, stream_mode="values"))


@pytest.mark.asyncio
async def test_v3_tokens_arrive_before_completion_and_close_cancels_node():
    model = FakeListChatModel(responses=["hello"])
    closed = asyncio.Event()

    async def work(state):
        try:
            result = await model.ainvoke(state["messages"])
            await asyncio.Event().wait()
            return {"messages": [result]}
        finally:
            closed.set()

    graph = compile_graph(work)
    async with aclosing(stream_graph(graph, {"messages": [HumanMessage(content="hi")]},
            config={}, stream_mode=["messages", "values"], protocol_events=True)) as events:
        async for mode, data in events:
            if mode == "messages":
                assert data[0].content == "h"
                assert not closed.is_set()
                break
    await asyncio.wait_for(closed.wait(), 1)


@pytest.mark.asyncio
async def test_v3_whole_messages_are_serializable_and_not_replayed_from_values():
    graph = compile_graph(lambda state: {"messages": [AIMessage(content="done", id="answer")]})
    events = [event async for mode, event in stream_graph(graph,
        {"messages": [HumanMessage(content="hi")]}, config={}, stream_mode=["values"], protocol_events=True)
        if mode == "protocol"]
    json.dumps(events)
    starts = [event for event in events if event["method"] == "messages"
              and event["params"]["data"][0].get("event") == "message-start"]
    assert len(starts) == 1


@pytest.mark.parametrize("metadata", [None, [], "invalid", 42, {"run_id": "model", "tags": ["visible"]}])
def test_whole_message_events_normalizes_metadata(metadata):
    events = whole_message_events(AIMessage(content="answer", id="answer"), metadata)
    expected = {**metadata} if isinstance(metadata, dict) else {}
    expected.setdefault("run_id", "answer")
    assert all(event["params"]["data"][1] == expected for event in events)
    assert events[-1]["params"]["data"][0]["event"] == "message-finish"
    json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, [], "invalid", 42, {"run_id": "model", "tags": ["visible"]}])
@pytest.mark.parametrize("payload", [
    AIMessage(content="answer", id="answer"),
    {"event": "message-start", "id": "answer"},
    {"event": "content-block-delta", "index": 0, "delta": {"type": "text-delta", "text": "answer"}},
    {"event": "message-finish"},
])
async def test_v3_adapter_normalizes_message_metadata(payload, metadata):
    class AdapterRun:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def __aiter__(self):
            yield {
                "method": "messages", "params": {"namespace": [], "data": [payload, metadata]},
            }

        async def output(self):
            return {}

        async def interrupted(self):
            return False

    async def astream_events(*args, **kwargs):
        return AdapterRun()

    graph = SimpleNamespace(astream_events=astream_events)
    events = [event async for event in stream_graph(graph, {}, config={},
        stream_mode=["messages"], protocol_events=True)]
    native = [event for mode, event in events if mode == "protocol" and event["method"] == "messages"]
    assert native
    expected = metadata if isinstance(metadata, dict) else {}
    for event in native:
        actual = event["params"]["data"][1]
        assert isinstance(actual, dict)
        assert all(actual[key] == value for key, value in expected.items())
    chunks = [data for mode, data in events if mode == "messages"]
    if isinstance(payload, dict) and payload["event"] in {"content-block-delta", "message-finish"}:
        assert len(chunks) == 1
        assert chunks[0][1] == {**expected, **({"message_finished": True} if payload["event"] == "message-finish" else {})}


@pytest.mark.asyncio
async def test_run_agent_full_forwards_options_and_emits_protocol_without_changing_final_text(monkeypatch):
    from agent.modules.agent_runtime import runner
    from agent.modules.tools import PermissionMode

    captured = {}

    async def application_updates(**params):
        captured.update(params)
        yield {"type": "thinking", "content": "reason"}
        yield {"type": "message", "content": "partial"}
        yield {"type": "final", "content": "answer"}

    monkeypatch.setattr(runner, "run_agent_stream", application_updates)
    params = {
        "user_input": "task", "thread_id": "child", "agent_name": "worker", "workflow": "react_agent",
        "workspace": {"type": "local", "path": "/workspace"}, "working_dir": "/workspace/project",
        "context_compact_threshold": 1000, "channel_context_trim_threshold": 2000,
        "allowed_tool_names": ["read"], "allowed_skill_names": ["review"],
        "provider": "provider", "model": "model", "reasoning_effort": "high",
        "permission_mode": PermissionMode.AUTO, "acp_config_options": {"mode": "auto"},
        "browser_owner_id": "owner", "attachments": [{"name": "file.txt"}],
        "usage_context": {"source": "test"}, "resume": True,
        "resume_payload": {"answer": "approved"}, "checkpoint_id": "checkpoint",
        "_checkpoint_metadata": {"k41_parent_thread_id": "parent"},
    }
    events = []
    result = await runner.run_agent_full(**params, _event_writer=events.append)
    assert result == "answer"
    assert captured == {**params, "emit_thinking": True, "_protocol_events": True}
    assert all(event["type"] == "event" for event in events)
    assert events[-1]["params"]["data"]["event"] == "completed"
    assert [projection["content"] for event in events if (projection := application_event(event))
            and projection["type"] == "final"] == ["answer"]


@pytest.mark.asyncio
async def test_v3_tools_expose_output_deltas_and_terminal_status():
    @tool
    def progress(runtime: ToolRuntime) -> str:
        """Emit progress and finish."""
        runtime.emit_output_delta("one")
        runtime.emit_output_delta("two")
        return "done"

    graph = compile_graph(ToolNode([progress]))
    events = [event async for mode, event in stream_graph(graph,
        {"messages": [AIMessage(content="", tool_calls=[{"name": "progress", "id": "call", "args": {}}])]},
        config={}, stream_mode=["values"], protocol_events=True) if mode == "protocol" and event["method"] == "tools"]
    assert [event["params"]["data"]["event"] for event in events] == [
        "tool-started", "tool-output-delta", "tool-output-delta", "tool-finished",
    ]
    assert events[-1]["params"]["data"]["output"]["status"] == "success"


@pytest.mark.asyncio
async def test_canonical_stream_preserves_application_updates_without_synthesizing_native_messages(monkeypatch):
    async def application_updates(**params):
        yield {"type": "thinking", "content": "reason"}
        yield {"type": "message", "content": "answer"}
        yield {"type": "final", "content": "answer"}

    monkeypatch.setattr("agent.modules.agent_runtime.runner.run_agent_stream", application_updates)
    events = [event async for event in run_agent_protocol_stream(thread_id="thread")]
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert all(event["event_id"].endswith(":" + str(event["seq"])) for event in events)
    assert [projected["type"] for event in events if (projected := application_event(event))] == ["thinking", "message", "final"]
    assert not any(event["method"] == "messages" for event in events)


@pytest.mark.asyncio
async def test_v3_subscribers_share_replay_and_cursor(monkeypatch):
    api = importlib.import_module("agent.delivery.http.api.router")
    v2 = importlib.import_module("agent.delivery.http.api.events_v2")
    from agent.modules.admin_auth import get_current_admin

    manager = ChatStreamManager()
    async def prepare(*args, **kwargs):
        return {"thread_id": "thread", "user_input": "hi"}
    async def application_updates(**params):
        yield {"type": "message", "content": "hello"}
        yield {"type": "final", "content": "hello"}
    monkeypatch.setattr(v2, "prepare_run_params", prepare)
    monkeypatch.setattr(v2, "get_chat_stream_manager", lambda: manager)
    monkeypatch.setattr("agent.modules.agent_runtime.runner.run_agent_stream", application_updates)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(v2.router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v2/api/chat/events", json={"message": "hi", "new_thread": True})
            assert response.status_code == 200
            original = [json.loads(line) for line in response.text.splitlines()]
            created = next(event for event in original if application_event(event) == {
                "type": "thread_created", "thread_id": "thread",
            })
            assert created["params"]["data"]["projection"] == "ui"
            run_id = response.headers["x-run-id"]
            assert original[0]["event_id"].startswith(run_id + ":")
            started = next(event for event in original if event["method"] == "lifecycle")
            assert started["params"]["data"]["run_id"] == run_id
            response = await client.post("/v2/api/chat/events/reconnect", json={"thread_id": "thread", "run_id": run_id, "after_seq": 3})
            assert [json.loads(line) for line in response.text.splitlines()] == [event for event in original if event["seq"] > 3]
            response = await client.post("/v1/api/chat/events/reconnect", json={"thread_id": "thread"})
            assert response.status_code == 404
            response = await client.post("/v2/api/chat/events/reconnect", json={"thread_id": "thread", "run_id": "old", "after_seq": 0})
            assert response.status_code == 409
            response = await client.post("/v2/api/chat/events/reconnect", json={"thread_id": "thread", "after_seq": 1})
            assert response.status_code == 422
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_delegation_links_survive_fork_and_source_deletion(monkeypatch):
    from agent.modules.agent_runtime.delegation import copy_delegated_history, delegation_reference, find_delegation
    from langchain_core.messages import ToolMessage
    saver = InMemorySaver()
    graph = compile_graph(lambda state: {"messages": [AIMessage(content="child result")]}, saver=saver)
    metadata = {"k41_parent_thread_id": "parent", "k41_parent_tool_call_id": "call",
                "k41_delegation_id": "delegation", "k41_agent_name": "child"}
    await graph.ainvoke({"messages": [HumanMessage(content="task")]},
        {"configurable": {"thread_id": "parent:sub:child:1"}, "metadata": metadata})
    monkeypatch.setattr("agent.modules.workflows.checkpoint.get_checkpointer", lambda: saver)
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda _: graph)
    async def graph_name(_):
        return "react_agent"
    monkeypatch.setattr("agent.modules.workflows.graph_name_for_thread", graph_name)
    checkpoint = await find_delegation("parent", delegation_id="delegation")
    state = {"messages": [ToolMessage(content="child result", name="delegate_task", tool_call_id="call",
                                      artifact={"subagent": delegation_reference(checkpoint)})]}
    await copy_delegated_history(state, "parent", "fork")
    await saver.adelete_thread("parent:sub:child:1")
    copied = await find_delegation("fork", delegation_id="delegation")
    assert copied is not None
    assert copied.config["configurable"]["thread_id"] == "fork:sub:child:1"
    assert state["messages"][0].artifact["subagent"]["parent_thread_id"] == "fork"
    assert len([item async for item in saver.alist(copied.config)]) < 10


@pytest.mark.asyncio
async def test_delegation_metadata_and_fork_are_supported_by_sqlite(monkeypatch):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from agent.modules.agent_runtime.delegation import (
        annotate_delegation_results, copy_delegated_history, find_delegation, get_subagent_history,
    )
    from langchain_core.messages import ToolMessage
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = compile_graph(lambda state: {"messages": [AIMessage(content="child result")]}, saver=saver)
        await graph.ainvoke({"messages": [HumanMessage(content="task")]}, {
            "configurable": {"thread_id": "parent:sub:child:1"}, "metadata": {
                "k41_parent_thread_id": "parent", "k41_parent_tool_call_id": "call",
                "k41_delegation_id": "delegation", "k41_agent_name": "child",
            },
        })
        monkeypatch.setattr("agent.modules.workflows.checkpoint.get_checkpointer", lambda: saver)
        monkeypatch.setattr("agent.modules.conversations.history.get_history_checkpointer", lambda: saver)
        monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda _: graph)
        async def graph_name(_):
            return "react_agent"
        monkeypatch.setattr("agent.modules.workflows.graph_name_for_thread", graph_name)
        checkpoint = await find_delegation("parent", delegation_id="delegation")
        message = await annotate_delegation_results(
            ToolMessage(content="done", name="delegate_task", tool_call_id="call"), "parent")
        assert message.artifact["subagent"]["checkpoint_id"] == checkpoint.config["configurable"]["checkpoint_id"]
        state = {"messages": [message]}
        await copy_delegated_history(state, "parent", "fork")
        await saver.adelete_thread("parent:sub:child:1")
        copied = await find_delegation("fork", delegation_id="delegation")
        assert copied is not None
        messages = copied.checkpoint["channel_values"]["messages"]
        assert messages[-1].content == "child result"
        history = await get_subagent_history("fork", "delegation")
        assert history["messages"][-1]["content"] == "child result"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_delegated_runtime_forwards_child_tokens_and_returns_final_text(monkeypatch, isolated_container, cancel):
    from agent.modules.agent_runtime import runner
    from agent.modules.tools.builtin.delegation.delegate_task import delegate_task
    from agent.modules.tools import PermissionMode
    from langchain_core.runnables import RunnableConfig
    saver = InMemorySaver()
    child_model = FakeListChatModel(responses=["child answer"])
    child_closed = asyncio.Event()
    async def child_work(state):
        try:
            message = await child_model.ainvoke(state["messages"])
            if cancel:
                await asyncio.Event().wait()
            return {"messages": [message]}
        finally:
            child_closed.set()
    child = compile_graph(child_work, saver=saver)
    tools = ToolNode([delegate_task])
    async def parent_work(state, config: RunnableConfig):
        call = AIMessage(content="", id="dispatch", tool_calls=[{
            "id": "dispatch-call", "name": "delegate_task", "args": {"task": "work", "sub_agent": "child"},
        }])
        result = await tools.ainvoke({"messages": [call]}, config=config)
        assert result["messages"][-1].content == "child answer"
        return {"messages": [call, *result["messages"], AIMessage(content="parent answer")]}
    parent = compile_graph(parent_work, saver=saver)
    catalog = SimpleNamespace(get_agent=lambda name: SimpleNamespace(graph_type=name, tools=[]),
                              validate_call=lambda caller, target: True)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: catalog)
    monkeypatch.setattr(runner, "get_workflow_graph", lambda name: child if name == "child" else parent)
    monkeypatch.setattr(runner, "make_run_context", lambda **kwargs: kwargs)
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context",
                        lambda runtime: SimpleNamespace(permission_mode=PermissionMode.AUTO))
    async def release(_):
        pass
    monkeypatch.setattr("agent.modules.browser.release_delegated_browser", release)
    events = []
    async with aclosing(run_agent_protocol_stream(user_input="hi", thread_id="parent", agent_name="parent")) as stream:
        async for event in stream:
            events.append(event)
            if cancel and event["params"]["namespace"] and event["method"] == "messages":
                if event["params"]["data"][0].get("event") == "content-block-delta":
                    break
    if cancel:
        await asyncio.wait_for(child_closed.wait(), 1)
        return
    child_events = [event for event in events if event["params"]["namespace"]]
    assert any(event["method"] == "messages" and event["params"]["data"][0].get("event") == "content-block-delta"
               for event in child_events)
    lifecycle = next(event["params"]["data"] for event in child_events if event["method"] == "lifecycle")
    assert lifecycle["tool_call_id"] == "dispatch-call"
    assert lifecycle["agent_name"] == "child"
    assert [projection["content"] for event in events if (projection := application_event(event))
            and projection["type"] == "final"] == ["parent answer"]
