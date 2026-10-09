"""Context percentage configuration, automatic compaction and channel retention."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph.message import add_messages
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from agent.modules.agents.models import AgentConfig
from agent.modules.agents.parser import AgentMarkdownError, parse_agent_markdown_content, serialize_agent_config
from agent.modules.conversations import compaction
from agent.modules.providers.context_window import resolve_context_window
from agent.modules.workflows import model_context
from agent.modules.workflows.nodes.trim import trim_channel_history
from agent.modules.workflows.run_config import WorkflowContext


def agent(**overrides):
    return AgentConfig(name="sample", graph_type="react_agent", provider="test", **overrides)


def history():
    return [
        HumanMessage(content="old request " * 100, id="h1"),
        AIMessage(content="old answer " * 100, id="a1"),
        HumanMessage(content="latest request", id="h2"),
        AIMessage(content="latest answer", id="a2"),
        HumanMessage(content="continue", id="h3"),
    ]


def resolved(limit=128_000):
    return SimpleNamespace(context_window=limit, provider_name="test", model_name="model-a")


async def prepare(messages, ctx=None, card=None, model=None, tools=None, system=None):
    return await model_context.prepare_model_context(
        messages, system=system or SystemMessage(content="system"),
        context=ctx or WorkflowContext(), agent_config=card or agent(),
        resolved=model or resolved(), config={"configurable": {"thread_id": "test-thread"}},
        tools=tools,
    )


@pytest.mark.asyncio
async def test_preparing_context_does_not_publish_token_estimates(monkeypatch):
    from langchain_core.tools import tool

    @tool
    def read_document(path: str) -> str:
        """Read the contents of a document at the given path."""
        return path

    events = []
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    messages = [HumanMessage(content="Read the document")]
    system = SystemMessage(content="Follow the user's instructions")
    await prepare(messages, tools=[read_document], system=system)
    assert not events


@pytest.mark.asyncio
async def test_context_usage_streams_only_after_the_provider_returns():
    from langgraph.graph import END, START, MessagesState, StateGraph

    release_model = asyncio.Event()
    model_started = asyncio.Event()

    async def model_node(state):
        await prepare(state["messages"])
        model_started.set()
        await release_model.wait()
        response = AIMessage(content="done", usage_metadata={"input_tokens": 123, "output_tokens": 5, "total_tokens": 128})
        model_context.emit_reported_context_usage(response, resolved=resolved(), config={})
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", model_node)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    stream = graph.compile().astream(
        {"messages": [HumanMessage(content="hello")]}, stream_mode="custom",
    )
    next_event = asyncio.create_task(anext(stream))
    try:
        await asyncio.wait_for(model_started.wait(), timeout=5)
        assert not next_event.done()
        release_model.set()
        event = await asyncio.wait_for(next_event, timeout=5)
        assert event["type"] == "context_usage"
        assert event["current_context_tokens"] == 128
        assert event["input_tokens"] == 123
        assert event["output_tokens"] == 5
        assert event["estimated"] is False
    finally:
        release_model.set()
        if not next_event.done():
            next_event.cancel()
            await asyncio.gather(next_event, return_exceptions=True)
        await stream.aclose()


def test_card_default_and_markdown_roundtrip():
    card = agent()
    assert card.context_compact_threshold == 75
    serialized = serialize_agent_config(card)
    assert "context_compact_threshold: 75" in serialized
    assert "context_trim_threshold" not in serialized
    assert "max_context_tokens" not in serialized
    assert parse_agent_markdown_content(serialized) == card
    assert parse_agent_markdown_content("---\nname: sample\nprovider: test\n---\nPrompt").context_compact_threshold == 75


def test_legacy_override_is_invalid_instead_of_falling_back_to_builtin(tmp_path):
    from agent.modules.agents.repository import FilesystemAgentRepository
    (tmp_path / "old-default.md").write_text(
        '---\nname: "default"\nprovider: test\ncontext_trim_threshold: 50000\n---\nPrompt',
        encoding="utf-8",
    )
    repository = FilesystemAgentRepository(tmp_path)
    assert "default" not in repository.load()
    card = next(item for item in repository.list_cards() if item.name == "default")
    assert not card.valid
    assert "Replace them with context_compact_threshold" in card.error


@pytest.mark.parametrize("value", [0, 101, 50_000, True, 75.5, "75"])
def test_card_and_runtime_reject_invalid_percentages(value):
    with pytest.raises(ValidationError):
        agent(context_compact_threshold=value)
    with pytest.raises(ValueError):
        WorkflowContext(context_compact_threshold=value)


@pytest.mark.parametrize("key", ["context_trim_threshold", "max_context_tokens"])
@pytest.mark.parametrize("has_new_key", [False, True])
def test_old_card_keys_require_explicit_migration(key, has_new_key):
    values = {key: 50_000}
    if has_new_key:
        values["context_compact_threshold"] = 75
    with pytest.raises(ValidationError, match="Replace them with context_compact_threshold"):
        agent(**values)
    markdown = f"---\nname: sample\nprovider: test\n{key}: 50000\n---\nPrompt"
    with pytest.raises(AgentMarkdownError, match="Replace them with context_compact_threshold"):
        parse_agent_markdown_content(markdown)
    from agent.delivery.http.dashboard.routes.agents import AgentCardBody
    with pytest.raises(ValidationError, match="Replace them"):
        AgentCardBody(name="sample", **values)


@pytest.mark.parametrize("offset, expected", [(-1, False), (0, True), (1, True)])
@pytest.mark.asyncio
async def test_trigger_below_at_and_above_threshold(monkeypatch, offset, expected):
    messages = history()
    tokens = count_tokens_approximately([SystemMessage(content="system"), *messages])
    # At 75%, this window places the input exactly on the trigger boundary.
    compact = AsyncMock(return_value=compaction.CompactedHistory(messages[-1:], "summary", 4, 1))
    monkeypatch.setattr(model_context, "compact_message_history", compact)
    # Shift the input count one token across a fixed budget without changing
    # the response reservation or tool accounting.
    window = (tokens * 100 + 74) // 75
    boundary = window * 75 // 100
    monkeypatch.setattr(model_context, "count_tokens_approximately", lambda items, **kwargs: (
        boundary + offset if len(items) > 2 else count_tokens_approximately(items, **kwargs)
    ))
    kept, updates = await prepare(messages, model=resolved(window))
    assert bool(compact.await_count) is expected
    assert bool(updates) is expected
    assert add_messages(messages, updates) == kept


@pytest.mark.asyncio
async def test_run_override_wins_and_model_changes_recalculate_limit(monkeypatch):
    messages = history()
    compact = AsyncMock(return_value=compaction.CompactedHistory(messages[-1:], "summary", 4, 1))
    monkeypatch.setattr(model_context, "compact_message_history", compact)
    await prepare(messages, ctx=WorkflowContext(context_compact_threshold=100), card=agent(context_compact_threshold=10), model=resolved(1000))
    assert compact.await_count == 0
    await prepare(messages, card=agent(context_compact_threshold=10), model=resolved(1000))
    assert compact.await_count == 1
    await prepare(messages, ctx=WorkflowContext(context_compact_threshold=100), model=resolved(100))
    assert compact.await_count == 2


@pytest.mark.asyncio
async def test_trigger_counts_rendered_system_and_tool_schemas(monkeypatch):
    messages = [HumanMessage(content="a"), AIMessage(content="b"), HumanMessage(content="c")]
    compact = AsyncMock(return_value=compaction.CompactedHistory(messages[-1:], "summary", 2, 1))
    monkeypatch.setattr(model_context, "compact_message_history", compact)
    await prepare(messages, model=resolved(1000))
    assert compact.await_count == 0
    with pytest.raises(compaction.CompactionBudgetError):
        await prepare(messages, system=SystemMessage(content="x" * 4000), model=resolved(1000))
    assert compact.await_count == 1
    with pytest.raises(compaction.CompactionBudgetError):
        await prepare(messages, tools=[{"name": "large", "description": "x" * 4000}], model=resolved(1000))
    assert compact.await_count == 2


@pytest.mark.asyncio
async def test_failed_compaction_preserves_history_and_retries(monkeypatch):
    messages = history()
    compact = AsyncMock(side_effect=compaction.CompactionSummaryError("timeout"))
    monkeypatch.setattr(model_context, "compact_message_history", compact)
    for _ in range(2):
        kept, updates = await prepare(messages, model=resolved(1000), card=agent(context_compact_threshold=10))
        assert kept is messages
        assert updates == []
    assert compact.await_count == 2


@pytest.mark.asyncio
async def test_non_reducing_compaction_does_not_replace_state(monkeypatch):
    messages = history()
    oversized = [HumanMessage(content="x" * 10000)]
    monkeypatch.setattr(model_context, "compact_message_history", AsyncMock(return_value=compaction.CompactedHistory(oversized, "summary", 4, 1)))
    kept, updates = await prepare(messages, model=resolved(1000), card=agent(context_compact_threshold=10))
    assert kept is messages
    assert updates == []


@pytest.mark.asyncio
async def test_channel_compacts_then_trims_only_once(monkeypatch):
    messages = history()
    order = []
    async def compact(items, **kwargs):
        order.append("compact")
        return compaction.CompactedHistory(items[-3:], "summary", 2, 3)
    def trim(items, limit):
        order.append("trim")
        assert items == messages[-3:]
        return items[-1:]
    monkeypatch.setattr(model_context, "compact_message_history", compact)
    monkeypatch.setattr(model_context, "trim_channel_history", trim)
    ctx = WorkflowContext(channel_context_trim_threshold=10)
    await prepare(messages, ctx=ctx, model=resolved(100))
    assert order == ["compact", "trim"]
    await prepare(messages, ctx=ctx, model=resolved(100))
    assert order == ["compact", "trim", "compact"]


@pytest.mark.asyncio
async def test_default_runs_never_trim(monkeypatch):
    def fail_trim(*args):
        raise AssertionError("Non-channel history must not be trimmed")
    monkeypatch.setattr(model_context, "trim_channel_history", fail_trim)
    kept, updates = await prepare(history())
    assert len(kept) == 5
    assert updates == []


@pytest.mark.parametrize("channel, budget", [("telegram", 1000), ("discord", 2000), ("zalo", 3000)])
def test_channel_settings_are_read_per_inbound_run(monkeypatch, channel, budget):
    from agent.modules.agent_runtime.runner import build_run_params
    from agent.shared.config import ConfigService, DefaultConfigSource
    config = ConfigService([DefaultConfigSource()])
    config.reload()
    monkeypatch.setattr("agent.shared.config.get_config_service", lambda: SimpleNamespace(get_int=lambda key, default: budget if key == f"channels.{channel}.context_trim_threshold" else default))
    params = build_run_params(platform=channel, user_id="user", user_input="hi")
    assert params["channel_context_trim_threshold"] == budget
    assert config.get_int(f"channels.{channel}.context_trim_threshold") == 50_000
    assert build_run_params(platform="api", user_id="user", user_input="hi")["channel_context_trim_threshold"] is None
    from agent.modules.channels.service_specs import BUILTIN_CHANNEL_DESCRIPTORS
    descriptor = next(item for item in BUILTIN_CHANNEL_DESCRIPTORS if item.name == channel)
    field = next(item for item in descriptor.settings_schema if item.name == "context_trim_threshold")
    assert field.section == "context"
    assert field.default == 50_000


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "50000"])
def test_dashboard_rejects_invalid_channel_budget(value):
    from agent.delivery.http.dashboard.routes.helpers.settings import normalize_setting_value
    with pytest.raises(HTTPException):
        normalize_setting_value("channels.telegram.context_trim_threshold", value)
    assert normalize_setting_value("channels.telegram.context_trim_threshold", None) is None
    assert normalize_setting_value("channels.telegram.context_trim_threshold", 12345) == 12345


def test_trim_preserves_oversized_tool_group():
    messages = [
        HumanMessage(content="old", id="h1"), AIMessage(content="old", id="a1"),
        HumanMessage(content="latest", id="h2"),
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "call"}], id="a2"),
        ToolMessage(content="x" * 10000, tool_call_id="call", id="t2"),
    ]
    trimmed = trim_channel_history(messages, 10)
    assert compaction.is_valid_message_sequence(trimmed)
    assert trimmed[-1] is messages[-1]
    assert any(isinstance(item, AIMessage) and item.tool_calls for item in trimmed)


def test_channel_budget_counts_model_content_instead_of_raw_file_payload():
    messages = [
        HumanMessage(content="read the file", id="h1"),
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "call"}], id="a1"),
        ToolMessage(content=[{"type": "text", "text": "small result"}, {"type": "file", "file_data": "x" * 10000}], tool_call_id="call", id="t1"),
    ]
    assert count_tokens_approximately(messages) > 100
    assert trim_channel_history(messages, 100) is messages


def test_context_limit_prefers_catalog_then_profile_then_fallback(monkeypatch):
    catalog = SimpleNamespace(models=[SimpleNamespace(id="known", context_window=200_000)])
    monkeypatch.setattr("agent.modules.providers.catalog.get_provider_catalog_entry", lambda provider: catalog)
    assert resolve_context_window("provider", "known", {"max_input_tokens": 100_000}) == 200_000
    assert resolve_context_window("provider", "custom", {"max_input_tokens": 64_000}) == 64_000
    assert resolve_context_window("provider", "custom") == 128_000
    assert resolve_context_window("provider", "custom", {"max_input_tokens": 0}) == 128_000


@pytest.mark.asyncio
async def test_repeated_compaction_includes_previous_summary(monkeypatch):
    calls = []
    async def summarize(items, **kwargs):
        calls.append(items)
        return "Preserved objectives and decisions"
    monkeypatch.setattr(compaction, "_generate_context_summary", summarize)
    first = await compaction.compact_message_history(history(), keep_recent_messages=2)
    next_history = [*first.messages, HumanMessage(content="next", id="h4"), AIMessage(content="response", id="a4"), HumanMessage(content="continue", id="h5")]
    second = await compaction.compact_message_history(next_history, keep_recent_messages=2)
    assert any(item.additional_kwargs.get("is_compact_summary") for item in calls[1])
    assert compaction.is_valid_message_sequence(second.messages)


@pytest.mark.asyncio
async def test_auto_compaction_can_shorten_a_long_active_tool_turn(monkeypatch):
    messages = [HumanMessage(content="old", id="old-h"), AIMessage(content="old", id="old-a"), HumanMessage(content="active objective", id="current")]
    for index in range(3):
        messages.extend([
            AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": f"call-{index}"}], id=f"ai-{index}"),
            ToolMessage(content="x" * 1200, tool_call_id=f"call-{index}", id=f"tool-{index}"),
        ])
    summarized = []
    async def summarize(items, **kwargs):
        summarized.extend(items)
        return "Active objective and previous tool findings"
    monkeypatch.setattr(compaction, "_generate_context_summary", summarize)
    result = await compaction.compact_message_history(messages, keep_recent_messages=4, max_retained_tokens=400)
    assert any(item.id == "current" for item in summarized)
    assert compaction.is_valid_message_sequence(result.messages)
    assert result.messages[-1].id == "tool-2"
    assert result.kept_count == 2


def test_incomplete_parallel_tool_results_are_not_a_safe_retained_window():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "one"}, {"name": "read", "args": {}, "id": "two"}]),
        ToolMessage(content="first result", tool_call_id="one"),
    ]
    assert not compaction.is_valid_message_sequence(messages)
    assert not compaction.is_valid_message_sequence([*messages, HumanMessage(content="next")])
    assert compaction.is_valid_message_sequence([*messages, ToolMessage(content="second result", tool_call_id="two")])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "error", "timeout"])
async def test_summarizer_failures_are_wrapped_without_modifying_history(monkeypatch, failure):
    async def invoke(messages, config):
        assert "context_compaction" in config["tags"]
        assert config["metadata"]["context_compaction"] is True
        if failure == "error":
            raise RuntimeError("upstream failed")
        if failure == "timeout":
            await asyncio.sleep(1)
        return AIMessage(content="")
    monkeypatch.setattr(compaction, "get_resolved_chat_model", lambda **kwargs: SimpleNamespace(model=SimpleNamespace(ainvoke=invoke), provider_name="test", model_name="model"))
    monkeypatch.setattr(compaction, "load_usage_context", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(compaction, "attach_usage_context", lambda config, identity: config)
    messages = history()
    original_ids = [item.id for item in messages]
    with pytest.raises(compaction.CompactionSummaryError):
        await compaction._generate_context_summary(messages, timeout_seconds=0.01)
    assert [item.id for item in messages] == original_ids


def test_internal_summary_chunks_do_not_enter_visible_or_thinking_stream():
    from agent.modules.agent_runtime.runner import _StreamingChunkExtractor
    extractor = _StreamingChunkExtractor()
    internal = extractor.extract((AIMessageChunk(content="secret summary"), {"context_compaction": True}))
    assert internal.text == internal.thinking == ""
    visible = extractor.extract((AIMessageChunk(content="visible response"), {}))
    assert visible.text == "visible response"


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_runner_emits_only_response_after_internal_summary(monkeypatch, streaming):
    from agent.modules.agent_runtime import runner
    from agent.modules.usage.service import build_usage_context
    summary, acknowledgement = compaction._build_summary_message_pair("internal history")
    class Graph:
        async def astream(self, payload, **kwargs):
            if not streaming:
                yield {"messages": [summary, acknowledgement]}
                yield {"messages": [summary, acknowledgement, AIMessage(content="visible response", id="final")]}
                return
            yield "values", payload
            yield "messages", (AIMessageChunk(content="internal history"), {"context_compaction": True})
            yield "messages", (AIMessageChunk(content="visible response"), {})
            yield "values", {"messages": [summary, acknowledgement, AIMessage(content="visible response", id="final")]}
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: agent()))
    monkeypatch.setattr(runner, "get_workflow_graph", lambda name: Graph())
    monkeypatch.setattr(runner, "_record_conversation_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "load_usage_context", AsyncMock(return_value=build_usage_context("api_test")))
    if streaming:
        events = [event async for event in runner.run_agent_stream("hi", "api_test", agent_name="sample")]
        assert [event["content"] for event in events if event["type"] == "message"] == ["visible response"]
        assert [event["content"] for event in events if event["type"] == "final"] == ["visible response"]
        assert not any("internal history" in str(event) for event in events)
    else:
        assert [event async for event in runner.run_agent("hi", "api_test", agent_name="sample")] == ["visible response"]


def test_github_migration_adds_percentage_without_reinterpreting_tokens(tmp_path: Path):
    from agent.modules.github.migrations import migrate_github_tables
    url = f"sqlite:///{(tmp_path / 'github.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE github_repository_bindings (id INTEGER PRIMARY KEY, context_trim_threshold INTEGER)"))
        connection.execute(text("INSERT INTO github_repository_bindings (id, context_trim_threshold) VALUES (1, 50000)"))
    migrate_github_tables(url)
    migrate_github_tables(url)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT context_trim_threshold, context_compact_threshold FROM github_repository_bindings")).one()
        assert row == (50_000, None)
    assert "context_compact_threshold" in {item["name"] for item in inspect(engine).get_columns("github_repository_bindings")}
    engine.dispose()


@pytest.mark.parametrize("value", [0, 101, 50_000, True, 75.5])
def test_github_api_rejects_invalid_percentage(value):
    from agent.delivery.http.dashboard.routes.github import GitHubRepositoryBindingBody
    with pytest.raises(ValidationError):
        GitHubRepositoryBindingBody(context_compact_threshold=value)


@pytest.mark.parametrize("override", [None, 60])
def test_routing_preserves_explicit_override_and_channel_scope(override):
    from agent.modules.workflows.graphs.router import _build_target_context
    parent = WorkflowContext(context_compact_threshold=override, channel_context_trim_threshold=12345)
    child = _build_target_context(parent, agent(context_compact_threshold=85))
    assert child.context_compact_threshold == override
    assert child.channel_context_trim_threshold == 12345
    parent.channel_trim_applied = True
    assert _build_target_context(parent, agent()).channel_trim_applied is True


@pytest.mark.asyncio
async def test_router_replacement_does_not_restore_compacted_messages(monkeypatch):
    from agent.modules.workflows.graphs import router
    from agent.modules.workflows.registry import GraphRegistry
    messages = history()
    reduced = [HumanMessage(content="summary", id="summary"), AIMessage(content="answer", id="answer")]
    graph = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": reduced}))
    monkeypatch.setattr(GraphRegistry, "get", lambda name: graph)
    monkeypatch.setattr(router, "_resolve_target_workflow", lambda target: "react_agent")
    result = await router.llm_call(
        {"messages": messages, "target_agent": agent()}, {},
        SimpleNamespace(context=WorkflowContext()),
    )
    assert add_messages(messages, result["messages"]) == reduced


@pytest.mark.asyncio
async def test_subagent_does_not_inherit_parent_channel_trim_or_percentage(monkeypatch):
    from agent.modules.tools.builtin.delegation.call_agent import call_agent
    captured = {}
    async def run(**kwargs):
        captured.update(kwargs)
        return "done"
    monkeypatch.setattr("agent.modules.agent_runtime.run_agent_full", run)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(validate_call=lambda caller, child: True, get_agent=lambda name: agent(context_compact_threshold=85)))
    parent = WorkflowContext(agent_name="parent", context_compact_threshold=60, channel_context_trim_threshold=12345)
    runtime = SimpleNamespace(context=parent, config={"configurable": {"thread_id": "telegram_user"}})
    await call_agent.coroutine(task="research", sub_agent="sample", runtime=runtime)
    assert "channel_context_trim_threshold" not in captured
    assert "context_compact_threshold" not in captured
    assert ":sub:sample:" in captured["thread_id"]


@pytest.mark.asyncio
async def test_react_graph_compacts_after_tool_growth_and_resumes_checkpoint(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from agent.modules.workflows.graphs import react_agent
    from agent.modules.workflows.nodes import llm as llm_module
    from agent.modules.workflows.registry import GraphRegistry

    inputs = []
    summary_inputs = []
    card = agent()
    class Model:
        def bind_tools(self, tools, **kwargs):
            return self
        async def ainvoke(self, messages, config):
            inputs.append(messages)
            usage = {"input_tokens": 3000 + len(inputs), "output_tokens": 10, "total_tokens": 3010 + len(inputs)}
            if len(inputs) == 1:
                return AIMessage(content="", tool_calls=[{"name": "grow", "args": {}, "id": "call"}], id="tool-call", usage_metadata=usage)
            return AIMessage(content="done", id=f"response-{len(inputs)}", usage_metadata=usage)
    async def summarize(messages, **kwargs):
        summary_inputs.append(messages)
        return "Prior objectives preserved"
    async def tools(self, name, **kwargs):
        return []
    async def tool_node(state, config, runtime):
        return {"messages": [ToolMessage(content="x" * 2200, tool_call_id="call", id="tool-result")]}
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: card))
    monkeypatch.setattr(llm_module, "get_resolved_chat_model", lambda **kwargs: SimpleNamespace(model=Model(), provider_name="test", provider_type="openai_compatible", model_name="model-a", context_window=1000, profile={}))
    monkeypatch.setattr(llm_module, "get_workflow_reasoning_effort_kwargs", lambda *args: {})
    monkeypatch.setattr(llm_module, "get_runtime_prompt_variable_values", AsyncMock(return_value={}))
    monkeypatch.setattr(llm_module, "build_llm_system_prompt", lambda **kwargs: "system")
    monkeypatch.setattr(llm_module.ToolResolver, "aresolve_for_agent", tools)
    monkeypatch.setattr(react_agent, "tool_node", tool_node)
    monkeypatch.setattr(compaction, "_generate_context_summary", summarize)
    monkeypatch.setattr(compaction, "get_default_keep_recent_messages", lambda: 2)
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())
    react_agent.build_react_graph(InMemorySaver())
    graph = GraphRegistry.get("react_agent")
    config = {"configurable": {"thread_id": "context-test"}}
    messages = history()
    context_events = []
    async for mode, event in graph.astream(
        {"messages": messages}, config=config, context=WorkflowContext(),
        stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            context_events.append(event)
        else:
            result = event
    assert len(inputs) == 2
    assert [event["estimated"] for event in context_events] == [False, False]
    assert [event["current_context_tokens"] for event in context_events] == [3011, 3012]
    for event in context_events:
        assert event["type"] == "context_usage"
        assert event["thread_id"] == "context-test"
        assert event["context_window"] == 1000
    assert summary_inputs
    assert any(item.additional_kwargs.get("is_compact_summary") for item in inputs[1])
    assert compaction.is_valid_message_sequence(result["messages"])
    assert "h1" not in {item.id for item in result["messages"]}
    snapshot = await graph.aget_state(config)
    assert snapshot.values["messages"] == result["messages"]
    resumed = await graph.ainvoke({"messages": [HumanMessage(content="next", id="h-next")]}, config=config, context=WorkflowContext())
    assert resumed["messages"][-1].content == "done"
    assert "h1" not in {item.id for item in resumed["messages"]}


@pytest.mark.asyncio
async def test_research_checks_context_before_both_model_calls(monkeypatch):
    from agent.modules.workflows.graphs import research
    calls = []
    async def prepare_context(items, **kwargs):
        calls.append(kwargs["system"].content)
        return items, []
    events = []
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(
        content="report", usage_metadata={"input_tokens": 123, "output_tokens": 10, "total_tokens": 133},
    )))
    selected = SimpleNamespace(model=model, provider_name="test", model_name="model-a")
    monkeypatch.setattr(research, "_resolve_runtime_model", lambda runtime: (selected, {}, agent()))
    monkeypatch.setattr(research, "prepare_model_context", prepare_context)
    runtime = SimpleNamespace(context=WorkflowContext())
    await research._research_node({"messages": history()}, {}, runtime)
    await research._summarize_node({"messages": history()}, {}, runtime)
    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert len(events) == 2
    assert all(not event["estimated"] and event["current_context_tokens"] == 133 for event in events)


def test_reported_context_usage_is_not_estimated_when_provider_usage_is_missing(monkeypatch):
    events = []
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    model_context.emit_reported_context_usage(AIMessage(content="hello"), resolved=resolved(), config={})
    assert not events
