"""Regression coverage for durable curated context, providers, and tool history."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent.modules.conversations import compaction
from agent.modules.tools.runtime.output_retention import retain_message
from agent.modules.usage import estimate_context_breakdown
from agent.modules.workflows.decision_context import (
    CONTEXT_TOOL_NAME,
    bind_decision_context_tool,
    make_decision_context_messages,
    restore_legacy_decision_context,
)
from agent.modules.workflows.history_trim import trim_channel_history
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.state.extensions import DecisionAgentState


def snapshot_history(message_id="user-1", content="Original file contents"):
    return [HumanMessage(content="Inspect the workspace", id=message_id),
            *make_decision_context_messages(message_id, content, source="test")]


def test_context_pair_has_stable_ids_and_does_not_change_other_tool_bindings():
    history = snapshot_history()
    assert compaction.is_valid_message_sequence(history)
    assert make_decision_context_messages("user-1", history[-1].content, source="test") == history[1:]
    assert make_decision_context_messages("user-2", "new")[1].tool_call_id != history[-1].tool_call_id
    existing = [SimpleNamespace(name="read")]
    assert bind_decision_context_tool(existing, {"messages": []}) is existing
    bound = bind_decision_context_tool(existing, {"messages": history})
    assert existing == [bound[0]]
    assert [item.name for item in bound] == ["read", CONTEXT_TOOL_NAME]
    assert bind_decision_context_tool(bound, {"messages": history}) == bound
    schema = convert_to_openai_tool(bound[-1])["function"]
    assert schema["parameters"]["properties"] == {}
    assert "Original file contents" not in json.dumps(schema)
    assert bind_decision_context_tool([], {"decision_message_id": "user-1"})[0] is bound[-1]
    assert bind_decision_context_tool([], {"decision_context_tool_call_id": "saved"})[0] is bound[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [True, False])
async def test_repeated_tool_call_replays_only_the_current_turn_snapshot(available):
    history = snapshot_history(content="bounded snapshot\n" * 6_000)
    if not available:
        history.append(HumanMessage(content="A new turn without a snapshot", id="next-user"))
    history.append(AIMessage(content="", tool_calls=[{
        "name": CONTEXT_TOOL_NAME, "args": {}, "id": "repeat-call",
    }]))
    state = {"messages": history}
    tools = bind_decision_context_tool([], state)
    graph = StateGraph(DecisionAgentState)
    graph.add_node("tool", ToolNode(tools))
    graph.add_edge(START, "tool")
    graph.add_edge("tool", END)
    response = (await graph.compile().ainvoke(state))["messages"][-1]
    assert response.tool_call_id == "repeat-call"
    if available:
        assert response.status == "success"
        assert response.content == history[2].content
        # The generic output presentation limit must not shorten an existing snapshot.
        assert await retain_message(response, SimpleNamespace()) is response
    else:
        assert response.status == "error"
        assert "read or list_dir" in response.content
        assert "bounded snapshot" not in response.content


@pytest.mark.asyncio
async def test_reference_only_checkpoint_upgrades_once_and_tolerates_deleted_files(tmp_path, monkeypatch):
    (tmp_path / "keep.py").write_text("original body", encoding="utf-8")
    history = [HumanMessage(content="Inspect", id="legacy-user"),
               AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "other-call"}]),
               ToolMessage(content="other tool data", name="read", tool_call_id="other-call")]
    state = {"messages": history, "decision_message_id": "legacy-user", "directory_tree": "keep.py",
             "included_files": [{"path": "keep.py"}, {"path": "deleted.py"},
                                {"path": "excerpt.py", "use_full_content": False, "excerpt": "short excerpt"}]}
    upgraded, updates = await restore_legacy_decision_context(state, history, str(tmp_path))
    assert history == state["messages"]
    assert len(upgraded) == len(history) + 2
    assert compaction.is_valid_message_sequence(upgraded)
    assert "original body" in upgraded[2].content
    assert "short excerpt" in upgraded[2].content
    assert '<file path="deleted.py">' not in upgraded[2].content
    assert updates["decision_context_tool_call_id"] == upgraded[2].tool_call_id

    def unexpected_read(*args, **kwargs):
        pytest.fail("A completed migration or compacted snapshot must never reread files")

    monkeypatch.setattr("agent.modules.workflows.nodes.decision.read_file_safe", unexpected_read)
    migrated = {**state, **updates, "messages": upgraded}
    assert await restore_legacy_decision_context(migrated, upgraded, str(tmp_path)) == (upgraded, {})
    # Recover an already delivered pair even if an old state omitted its marker.
    assert await restore_legacy_decision_context({**state, "messages": upgraded}, upgraded, str(tmp_path)) == (upgraded, updates)
    compacted = [history[0], AIMessage(content="Compacted file context")]
    assert await restore_legacy_decision_context({**migrated, "messages": compacted}, compacted, str(tmp_path)) == (compacted, {})
    next_turn = [*history, HumanMessage(content="Next", id="next-user")]
    assert await restore_legacy_decision_context({**state, "messages": next_turn}, next_turn, str(tmp_path)) == (next_turn, {})


@pytest.mark.asyncio
async def test_compaction_and_channel_trim_keep_complete_pairs_without_restoring_old_context(monkeypatch):
    old = [*snapshot_history("old-user", "old snapshot " * 500), AIMessage(content="Old answer", id="old-ai")]
    recent = [*snapshot_history("recent-user", "recent snapshot"), AIMessage(content="Recent answer", id="recent-ai")]
    history = [*old, *recent]
    monkeypatch.setattr(compaction, "_generate_context_summary", AsyncMock(return_value="Earlier files were inspected."))
    compacted = await compaction.compact_message_history(history, keep_recent_messages=4)
    assert compaction.is_valid_message_sequence(compacted.messages)
    assert old[2] not in compacted.messages
    assert recent[2] in compacted.messages
    for limit in range(1, len(history) + 1):
        trimmed = trim_channel_history(history, limit, token_counter=len)
        assert compaction.is_valid_message_sequence(trimmed)
    state = {"messages": compacted.messages, "decision_message_id": "recent-user",
             "decision_context_tool_call_id": recent[2].tool_call_id}
    assert await restore_legacy_decision_context(state, compacted.messages, "missing-workspace") == (compacted.messages, {})
    counts = estimate_context_breakdown([SystemMessage(content="Static prompt"), *history])
    without_snapshots = estimate_context_breakdown([SystemMessage(content="Static prompt"), history[0]])
    assert counts["system_prompt"] == without_snapshots["system_prompt"]
    assert counts["tool_calls"] > without_snapshots["tool_calls"]


@pytest.mark.asyncio
async def test_prompt_estimate_does_not_hydrate_legacy_file_references(tmp_path, monkeypatch):
    from agent.modules.workflows import prompt_builders
    from agent.modules.workspaces import WorkspaceRef

    card = SimpleNamespace(name="curator", system_prompt="Static prompt")
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: card))
    monkeypatch.setattr("agent.modules.workspaces.get_thread_workspace_ref", AsyncMock(return_value=WorkspaceRef(
        backend="local", locator=str(tmp_path))))
    monkeypatch.setattr("agent.modules.tools.ToolResolver.aresolve_for_agent", AsyncMock(return_value=[]))
    monkeypatch.setattr("agent.modules.prompt_variables.get_runtime_prompt_variable_values", AsyncMock(return_value={}))
    monkeypatch.setattr("agent.modules.projects.project_secret_context", AsyncMock(return_value=""))
    monkeypatch.setattr(prompt_builders, "build_llm_system_prompt", lambda **kwargs: "Static prompt")

    def unexpected_read(*args, **kwargs):
        pytest.fail("Fixed prompt accounting must not read curated file bodies")

    monkeypatch.setattr("agent.modules.workflows.nodes.decision.read_file_safe", unexpected_read)
    state = {"messages": [HumanMessage(content="Inspect", id="legacy-user")], "decision_message_id": "legacy-user",
             "included_files": [{"path": "example.py"}], "directory_tree": "example.py"}
    counts = await prompt_builders.estimate_thread_prompt_breakdown("thread", agent_name="curator", state=state)
    assert counts["system_prompt"] == estimate_context_breakdown([SystemMessage(content="Static prompt")])["system_prompt"]
    assert counts["system_tools"] > 0
    assert counts["tool_calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai-chat", "openai-responses", "chatgpt", "anthropic",
                                      "gemini-3-flash-preview", "gemini-flash-lite-latest", "gemma-4-31b-it"])
async def test_curated_pair_survives_provider_serialization_without_other_tools(provider):
    history = [SystemMessage(content="Static prompt"), *snapshot_history()]
    original = [message.model_dump() for message in history]
    tools = bind_decision_context_tool([], {"messages": history})
    history = normalize_messages_for_chat_model(history)
    call_id = history[-1].tool_call_id
    next_history = [*history, AIMessage(content="Done"), HumanMessage(content="Next", id="next-user"),
                    *make_decision_context_messages("next-user", "Updated file contents")]
    if provider.startswith("openai") or provider == "chatgpt":
        from agent.modules.providers.openai_compatible.chat_model import OpenAICompatibleChatModel
        from agent.modules.providers.chatgpt.chat_model import ChatGPTChatModel

        cls = ChatGPTChatModel if provider == "chatgpt" else OpenAICompatibleChatModel
        model = cls(model="gpt-6", api_key="test-key", use_responses_api=provider != "openai-chat")
        request = model._get_request_payload(history, **model.bind_tools(tools).kwargs)
        subsequent = model._get_request_payload(next_history, **model.bind_tools(tools).kwargs)
        history_key = "messages" if provider == "openai-chat" else "input"
        assert subsequent[history_key][:len(request[history_key])] == request[history_key]
        if provider == "openai-chat":
            call, response = request["messages"][-2:]
            assert call["tool_calls"][0]["function"] == {"name": CONTEXT_TOOL_NAME, "arguments": "{}"}
            assert response["tool_call_id"] == call_id
            assert response["content"] == "Original file contents"
        else:
            call, response = request["input"][-2:]
            assert call["type"] == "function_call"
            assert call["name"] == CONTEXT_TOOL_NAME
            assert call["call_id"] == response["call_id"] == call_id
            assert response["output"] == "Original file contents"
        serialized = json.dumps(request)
        assert "decision_source" not in serialized
    elif provider == "anthropic":
        from agent.modules.providers.anthropic.chat_model import AnthropicChatModel

        model = AnthropicChatModel(model_name="claude-sonnet-4-5", api_key="test-key")
        request = model._get_request_payload(history, **model.bind_tools(tools).kwargs)
        subsequent = model._get_request_payload(next_history, **model.bind_tools(tools).kwargs)
        assert subsequent["messages"][:len(request["messages"])] == request["messages"]
        assert subsequent["tools"] == request["tools"]
        call, response = (item["content"][0] for item in request["messages"][-2:])
        assert request["system"] == "Static prompt"
        assert request["tools"][0]["name"] == CONTEXT_TOOL_NAME
        assert call["id"] == response["tool_use_id"] == call_id
        assert response["content"] == "Original file contents"
    else:
        from agent.modules.providers.google.chat_model import GoogleChatModel

        model = GoogleChatModel(model=provider, google_api_key="test-key")
        try:
            request = model._prepare_request(history, **model.bind_tools(tools).kwargs)
            call, response = (item.parts[0] for item in request["contents"][-2:])
            assert request["config"].system_instruction.parts[0].text == "Static prompt"
            assert call.function_call.name == response.function_response.name == CONTEXT_TOOL_NAME
            assert response.function_response.response == {"output": "Original file contents"}
            assert call.thought_signature == (None if provider.startswith("gemma") else "skip_thought_signature_validator")
            subsequent = model._prepare_request(next_history, **model.bind_tools(tools).kwargs)
            assert subsequent["contents"][:len(request["contents"])] == request["contents"]
            assert subsequent["config"].tools == request["config"].tools
        finally:
            await model.aclose()
    assert [message.model_dump() for message in history] == original


@pytest.mark.asyncio
async def test_virtual_tool_stream_and_serialized_history_are_visible_once():
    from agent.modules.agent_runtime.protocol import stream_graph
    from agent.modules.conversations.history import _serialize_thread_messages

    history = snapshot_history()
    graph = StateGraph(DecisionAgentState)
    graph.add_node("curate", lambda state: {"messages": history[1:], "decision_message_id": "user-1",
                                           "decision_context_tool_call_id": history[2].tool_call_id})
    graph.add_node("answer", lambda state: {"messages": [AIMessage(content="Done", id="answer")]})
    graph.add_edge(START, "curate")
    graph.add_edge("curate", "answer")
    graph.add_edge("answer", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "context-stream"}}
    events = [event async for mode, event in stream_graph(compiled, {"messages": history[:1]},
              config=config, stream_mode=["values"], protocol_events=True) if mode == "protocol"]
    starts = [event for event in events if event["method"] == "messages"
              and event["params"]["data"][0].get("event") == "content-block-start"
              and event["params"]["data"][0].get("content", {}).get("name") == CONTEXT_TOOL_NAME]
    finishes = [event for event in events if event["method"] == "tools"
                and event["params"]["data"].get("event") == "tool-finished"
                and event["params"]["data"].get("tool_call_id") == history[2].tool_call_id]
    assert len(starts) == len(finishes) == 1
    saved = (await compiled.aget_state(config)).values["messages"]
    serialized = _serialize_thread_messages(saved)
    assert serialized[1]["tool_calls"][0]["name"] == CONTEXT_TOOL_NAME
    assert serialized[2]["role"] == "tool"
    assert serialized[2]["content"] == history[2].content
