"""Regression coverage for compaction budgets, retained facts, and concurrent writes."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from agent.modules.agent_runtime.active_sessions import (
    ActiveSession,
    ActiveSessionRegistry,
    ThreadMutationConflictError,
)
from agent.modules.conversations import compaction, service
from agent.modules.providers.context_budget import model_input_budget
from agent.modules.workflows import model_context


def large_history():
    return [
        HumanMessage(content="Keep all constraints. " * 300, id="old-user"),
        AIMessage(content="Checked earlier work. " * 300, id="old-ai"),
        HumanMessage(content="Continue the pending task", id="recent-user"),
        AIMessage(content="Latest verified result", id="recent-ai"),
        HumanMessage(content="Proceed", id="latest-user"),
    ]


def session(thread_id="test-thread"):
    return ActiveSession(thread_id=thread_id, platform="test", user_id="test", channel_id="test", agent_name="default")


@pytest_asyncio.fixture(params=["memory", "sqlite"])
async def checkpoint_saver(request):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    if request.param == "memory":
        yield InMemorySaver()
    else:
        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            yield saver


@pytest.fixture
def manual_compaction(monkeypatch):
    registry = ActiveSessionRegistry()
    checkpoint = {"id": "checkpoint-before", "channel_versions": {"messages": 1}, "channel_values": {"messages": large_history(), "todos": []}}
    saved = SimpleNamespace(checkpoint=checkpoint, pending_writes=[])
    checkpointer = SimpleNamespace(aget_tuple=AsyncMock(return_value=saved))
    graph = SimpleNamespace(aupdate_state=AsyncMock(return_value={"configurable": {"checkpoint_id": "checkpoint-after"}}))
    payload = AsyncMock(return_value=([{"role": "user", "content": "summary"}], "checkpoint-after"))
    summary = AsyncMock(return_value="Preserved constraints and next steps")
    resolved = SimpleNamespace(context_window=128_000, provider_name="test", model_name="test-model")
    monkeypatch.setattr(compaction, "get_active_session_registry", lambda: registry)
    monkeypatch.setattr(compaction, "get_history_checkpointer", lambda: checkpointer)
    monkeypatch.setattr(compaction, "get_workflow_graph", lambda name: graph)
    monkeypatch.setattr(compaction, "get_thread_messages_payload", payload)
    monkeypatch.setattr(compaction, "_generate_context_summary", summary)
    monkeypatch.setattr(compaction, "get_resolved_chat_model", lambda **kwargs: resolved)
    monkeypatch.setattr(compaction, "get_conversation_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(compaction, "has_pending_interrupt", AsyncMock(return_value=False))
    monkeypatch.setattr(compaction, "get_default_keep_recent_messages", lambda: 2)
    return SimpleNamespace(registry=registry, checkpoint=checkpoint, saved=saved, checkpointer=checkpointer,
                           graph=graph, payload=payload, summary=summary, resolved=resolved)


def test_response_reserve_and_headroom_apply_even_at_100_percent():
    resolved = SimpleNamespace(context_window=10_000, model=SimpleNamespace(max_tokens=2000))
    assert model_input_budget(resolved, 100) == 7800
    assert model_input_budget(resolved, 75) == 7500
    resolved.model = SimpleNamespace(model_kwargs={"max_completion_tokens": 3000})
    assert model_input_budget(resolved) == 6800
    resolved.model = SimpleNamespace(max_output_tokens=11_000)
    assert model_input_budget(resolved) == 0


@pytest.mark.parametrize("provider_type, limit_key", [("google", "max_output_tokens"), ("anthropic", "max_tokens"), ("openai", "max_tokens")])
@pytest.mark.asyncio
async def test_summary_output_limit_uses_provider_request_parameter(monkeypatch, provider_type, limit_key):
    invoke = AsyncMock(return_value=AIMessage(content="Preserved constraints"))
    monkeypatch.setattr(compaction, "get_resolved_chat_model", lambda **kwargs: SimpleNamespace(
        model=SimpleNamespace(ainvoke=invoke), provider_type=provider_type, provider_name="test", model_name="test-model"))
    monkeypatch.setattr(compaction, "load_usage_context", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(compaction, "attach_usage_context", lambda config, identity: config)
    await compaction._generate_context_summary(large_history(), max_summary_tokens=200)
    assert invoke.await_args.kwargs[limit_key] == 200
    assert invoke.await_args.kwargs["config"]["metadata"]["context_compaction"]
    assert "Keep the summary within 200 tokens" in invoke.await_args.args[0][0].content


@pytest.mark.asyncio
async def test_complete_summary_and_recent_history_fit_budget(monkeypatch):
    summary = AsyncMock(side_effect=["s" * 4000, "Important constraints; continue pending task"])
    monkeypatch.setattr(compaction, "_generate_context_summary", summary)
    messages = large_history()
    result = await compaction.compact_message_history(messages, keep_recent_messages=2, max_retained_tokens=250)
    assert count_tokens_approximately(result.messages) <= 250
    assert result.messages[-1].id == "latest-user"
    assert compaction.is_valid_message_sequence(result.messages)
    assert summary.await_count == 2
    assert summary.await_args_list[1].kwargs["max_summary_tokens"] < summary.await_args_list[0].kwargs["max_summary_tokens"]


@pytest.mark.asyncio
async def test_persistently_oversized_summary_is_rejected_without_mutating_history(monkeypatch):
    messages = large_history()
    original = copy.deepcopy(messages)
    summary = AsyncMock(return_value="s" * 8000)
    monkeypatch.setattr(compaction, "_generate_context_summary", summary)
    with pytest.raises(compaction.CompactionBudgetError):
        await compaction.compact_message_history(messages, max_retained_tokens=250)
    assert messages == original
    assert summary.await_count == 3


@pytest.mark.asyncio
async def test_oversized_latest_tool_group_is_never_cut_or_sent_to_model(monkeypatch):
    messages = [*large_history(),
                AIMessage(content="", tool_calls=[{"id": "call", "name": "read", "args": {}}]),
                ToolMessage(content="x" * 8000, tool_call_id="call")]
    summary = AsyncMock()
    monkeypatch.setattr(compaction, "_generate_context_summary", summary)
    with pytest.raises(compaction.CompactionBudgetError):
        await compaction.compact_message_history(messages, max_retained_tokens=500)
    summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_reducing_result_above_input_capacity_is_not_accepted(monkeypatch):
    messages = large_history()
    oversized = [HumanMessage(content="s" * 4000)]
    monkeypatch.setattr(model_context, "compact_message_history", AsyncMock(return_value=compaction.CompactedHistory(oversized, "summary", 4, 1)))
    with pytest.raises(compaction.CompactionBudgetError):
        await model_context.prepare_model_context(messages, system=compaction.SystemMessage(content="system"),
            context=SimpleNamespace(), agent_config=SimpleNamespace(context_compact_threshold=75),
            resolved=SimpleNamespace(context_window=1000, provider_name="test", model_name="test"), config={})


@pytest.mark.asyncio
async def test_failed_compaction_above_input_capacity_stops_before_model(monkeypatch):
    monkeypatch.setattr(model_context, "compact_message_history", AsyncMock(side_effect=compaction.CompactionSummaryError("timeout")))
    with pytest.raises(compaction.CompactionBudgetError):
        await model_context.prepare_model_context(large_history(), system=compaction.SystemMessage(content="system"),
            context=SimpleNamespace(), agent_config=SimpleNamespace(context_compact_threshold=75),
            resolved=SimpleNamespace(context_window=1000, provider_name="test", model_name="test"), config={})


def test_summary_keeps_tool_arguments_status_middle_errors_tail_and_output_references():
    messages = [
        AIMessage(content="", tool_calls=[{"id": "call", "name": "execute", "args": {"command": "uv run pytest", "working_dir": "src/project"}}]),
        ToolMessage(content="a" * 5000 + "\nERROR: essential middle diagnostic\n" + "b" * 9000 + "\nFAILED: final assertion", tool_call_id="call", status="error",
                    artifact={"error": {"code": "TEST_FAILURE"}, "output_paths": [".k41-agent/outputs/full.log"]},
                    additional_kwargs={"output_retention": {"output_paths": [".k41-agent/outputs/full.log"]}}),
    ]
    transcript = compaction._format_messages_for_summary(messages)
    for expected in ("uv run pytest", "src/project", "Tool (execute)", "status: error", "essential middle diagnostic", "final assertion", "TEST_FAILURE", ".k41-agent/outputs/full.log"):
        assert expected in transcript
    assert "a" * 5000 not in transcript


def test_summary_keeps_media_references_without_binary_payloads():
    message = HumanMessage(content=[
        {"type": "text", "text": "Check the diagram"},
        {"type": "image_url", "image_url": {"url": "https://example.test/diagram.png"}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "BINARY_SECRET"}},
    ], additional_kwargs={"attachments": [{"filename": "diagram.png", "file_path": "assets/diagram.png", "url": "data:image/png;base64,BINARY_SECRET", "data": "BINARY_SECRET"}]})
    transcript = compaction._format_messages_for_summary([message])
    assert "https://example.test/diagram.png" in transcript
    assert "assets/diagram.png" in transcript
    assert "BINARY_SECRET" not in transcript


@pytest.mark.parametrize("change", ["content", "checkpoint_id", "version", "other_state", "pending_write", "deleted"])
@pytest.mark.asyncio
async def test_changed_checkpoint_aborts_even_when_message_ids_match(manual_compaction, change):
    env = manual_compaction
    fresh = copy.deepcopy(env.saved)
    if change == "content":
        fresh.checkpoint["channel_values"]["messages"][0].content = "Corrected user constraint"
    elif change == "checkpoint_id":
        fresh.checkpoint["id"] = "checkpoint-newer"
    elif change == "version":
        fresh.checkpoint["channel_versions"]["messages"] = 2
    elif change == "other_state":
        fresh.checkpoint["channel_values"]["todos"] = [{"content": "new task"}]
    elif change == "pending_write":
        fresh.pending_writes = [("task", "messages", HumanMessage(content="new input"))]
    elif change == "deleted":
        fresh = None
    env.checkpointer.aget_tuple.side_effect = [env.saved, fresh]
    with pytest.raises(compaction.CompactionConflictError):
        await compaction.compact_conversation_thread("test-thread")
    env.graph.aupdate_state.assert_not_awaited()
    assert not env.registry._thread_mutations


@pytest.mark.asyncio
async def test_manual_compaction_pins_checked_checkpoint_and_reads_its_result(manual_compaction):
    env = manual_compaction
    await compaction.compact_conversation_thread("test-thread")
    assert env.graph.aupdate_state.await_args.args[0]["configurable"]["checkpoint_id"] == "checkpoint-before"
    assert env.payload.await_args.kwargs["checkpoint_id"] == "checkpoint-after"
    assert not env.registry._thread_mutations


@pytest.mark.asyncio
async def test_manual_compaction_roundtrip_with_real_langgraph_checkpoints(manual_compaction, monkeypatch, checkpoint_saver):
    from langgraph.graph import END, START, MessagesState, StateGraph
    from agent.modules.conversations.history import get_thread_messages_payload

    saver = checkpoint_saver
    builder = StateGraph(MessagesState)
    builder.add_node("llm", lambda state: {"messages": []})
    builder.add_edge(START, "llm")
    builder.add_edge("llm", END)
    graph = builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "test-thread"}}
    before_config = await graph.aupdate_state(config, {"messages": large_history()}, as_node="llm")
    before_id = before_config["configurable"]["checkpoint_id"]
    monkeypatch.setattr(compaction, "get_history_checkpointer", lambda: saver)
    monkeypatch.setattr(compaction, "get_workflow_graph", lambda name: graph)
    monkeypatch.setattr(compaction, "get_thread_messages_payload", get_thread_messages_payload)
    monkeypatch.setattr("agent.modules.conversations.history.get_history_checkpointer", lambda: saver)
    result = await compaction.compact_conversation_thread("test-thread")
    latest = await graph.aget_state(config)
    original = await graph.aget_state(before_config)
    assert result["active_checkpoint_id"] != before_id
    assert latest.config["configurable"]["checkpoint_id"] == result["active_checkpoint_id"]
    assert latest.values["messages"][0].additional_kwargs["is_compact_summary"]
    assert latest.values["messages"][-1].id == "latest-user"
    assert original.values["messages"] == large_history()


@pytest.mark.asyncio
async def test_manual_compaction_rejects_non_reducing_summary(manual_compaction):
    env = manual_compaction
    env.summary.return_value = "s" * 100_000
    with pytest.raises(compaction.CompactionBudgetError):
        await compaction.compact_conversation_thread("test-thread")
    env.graph.aupdate_state.assert_not_awaited()
    assert not env.registry._thread_mutations


@pytest.mark.asyncio
async def test_manual_compaction_reserves_latest_prompt_cost(manual_compaction, monkeypatch):
    env = manual_compaction
    env.resolved.context_window = 1000
    monkeypatch.setattr("agent.modules.usage.get_usage_service", lambda: SimpleNamespace(
        get_thread_usage=AsyncMock(return_value={"context_breakdown": {"system_prompt": 800}})))
    with pytest.raises(compaction.CompactionBudgetError, match="System prompt"):
        await compaction.compact_conversation_thread("test-thread")
    env.summary.assert_not_awaited()
    env.graph.aupdate_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_runs_and_duplicate_compaction_are_excluded_and_cancellation_releases_reservation(manual_compaction):
    env = manual_compaction
    started = asyncio.Event()
    async def summarize(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    env.summary.side_effect = summarize
    task = asyncio.create_task(compaction.compact_conversation_thread("test-thread"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(ThreadMutationConflictError):
            env.registry.acquire(session())
        with pytest.raises(ThreadMutationConflictError):
            env.registry.register(session())
        with pytest.raises(compaction.CompactionConflictError):
            await compaction.compact_conversation_thread("test-thread")
        # The registry also excludes workers running on a separate OS thread.
        with pytest.raises(ThreadMutationConflictError):
            await asyncio.to_thread(env.registry.acquire, session())
        env.registry.acquire(session("another-thread"))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    env.graph.aupdate_state.assert_not_awaited()
    assert not env.registry._thread_mutations
    env.registry.acquire(session())


@pytest.mark.asyncio
async def test_running_session_prevents_manual_compaction(manual_compaction):
    env = manual_compaction
    env.registry.acquire(session())
    with pytest.raises(compaction.CompactionConflictError, match="agent is running"):
        await compaction.compact_conversation_thread("test-thread")
    env.checkpointer.aget_tuple.assert_not_awaited()
    assert not env.registry._thread_mutations


@pytest.mark.asyncio
async def test_background_message_injection_waits_for_compaction_and_is_not_lost(monkeypatch):
    registry = ActiveSessionRegistry()
    update = AsyncMock()
    monkeypatch.setattr("agent.modules.agent_runtime.active_sessions.get_active_session_registry", lambda: registry)
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda name: SimpleNamespace(aupdate_state=update))
    with registry.reserve_thread_mutation("test-thread"):
        task = asyncio.create_task(service.inject_agent_message_pair(thread_id="test-thread", human_content="request", ai_content="result"))
        await asyncio.sleep(0)
        update.assert_not_awaited()
        assert not task.done()
    await asyncio.wait_for(task, 2)
    update.assert_awaited_once()
    assert update.await_args.args[1]["messages"][-1].content == "result"
    assert not registry._thread_mutations


@pytest.mark.parametrize("tree", [False, True])
@pytest.mark.asyncio
async def test_checkpoint_deletion_waits_for_compaction(monkeypatch, tree):
    from agent.modules import workflows

    registry = ActiveSessionRegistry()
    delete = AsyncMock()
    clear = AsyncMock()
    monkeypatch.setattr("agent.modules.agent_runtime.active_sessions.get_active_session_registry", lambda: registry)
    monkeypatch.setattr("agent.modules.workflows.checkpoint.store.get_checkpointer", lambda: SimpleNamespace(adelete_thread=delete))
    monkeypatch.setattr(workflows, "_list_workflow_child_thread_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr("agent.modules.tools.clear_conversation_storage", clear)
    delete_thread = workflows.delete_workflow_thread_tree if tree else workflows.delete_workflow_thread
    with registry.reserve_thread_mutation("test-thread"):
        task = asyncio.create_task(delete_thread("test-thread"))
        await asyncio.sleep(0)
        delete.assert_not_awaited()
        clear.assert_not_awaited()
    await asyncio.wait_for(task, 2)
    delete.assert_awaited_once_with("test-thread")
    assert not registry._thread_mutations
