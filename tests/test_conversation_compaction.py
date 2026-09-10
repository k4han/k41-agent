"""Unit tests for conversation compaction service and API endpoint."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent.modules.conversations.compaction import (
    DEFAULT_KEEP_RECENT_MESSAGES,
    CompactionConflictError,
    CompactionSummaryError,
    compact_conversation_thread,
    get_default_keep_recent_messages,
    has_pending_interrupt,
    is_valid_message_sequence,
    resolve_compaction_cutoff,
    _format_messages_for_summary,
    _build_summary_message_pair,
)


def _make_sample_messages():
    return [
        HumanMessage(content="Hello, I want to build a feature.", id="msg-1"),
        AIMessage(
            content="Sure! Let me inspect files.",
            tool_calls=[{"id": "call-1", "name": "list_dir", "args": {"path": "."}}],
            id="msg-2",
        ),
        ToolMessage(content="file1.py\nfile2.py", tool_call_id="call-1", name="list_dir", id="msg-3"),
        AIMessage(content="I see the files. Let me read file1.", tool_calls=[{"id": "call-2", "name": "read_file", "args": {}}], id="msg-4"),
        ToolMessage(content="def foo(): pass", tool_call_id="call-2", name="read_file", id="msg-5"),
        AIMessage(content="File read successfully. How should we proceed?", id="msg-6"),
        HumanMessage(content="Please refactor foo to return 42.", id="msg-7"),
        AIMessage(content="Done refactoring foo to return 42.", id="msg-8"),
    ]


def test_format_messages_for_summary():
    messages = _make_sample_messages()[:3]
    formatted = _format_messages_for_summary(messages)
    assert "User: Hello, I want to build a feature." in formatted
    assert "Assistant: Sure! Let me inspect files. [Invoked tools: list_dir]" in formatted
    assert "Tool (list_dir): file1.py" in formatted


def test_build_summary_message_pair():
    human_msg, ai_msg = _build_summary_message_pair("Summary content here.")
    assert "[Conversation Context Summary]" in human_msg.content
    assert "Summary content here." in human_msg.content
    assert human_msg.additional_kwargs.get("is_compact_summary") is True
    assert ai_msg.additional_kwargs.get("is_compact_summary") is True


@pytest.mark.asyncio
async def test_compact_raises_on_empty_thread_id():
    with pytest.raises(ValueError, match="thread_id is required"):
        await compact_conversation_thread("")


@pytest.mark.asyncio
async def test_compact_raises_on_missing_checkpoint():
    with patch("agent.modules.conversations.compaction.get_history_checkpointer") as mock_chk:
        mock_chk.return_value.aget_tuple = AsyncMock(return_value=None)
        with pytest.raises(LookupError, match="No checkpoint found"):
            await compact_conversation_thread("thread-missing")


@pytest.mark.asyncio
async def test_compact_raises_when_conversation_too_short():
    short_messages = [
        HumanMessage(content="Hi", id="1"),
        AIMessage(content="Hello", id="2"),
    ]
    mock_tuple = MagicMock()
    mock_tuple.checkpoint = {"channel_values": {"messages": short_messages}}

    with patch("agent.modules.conversations.compaction.get_history_checkpointer") as mock_chk:
        mock_chk.return_value.aget_tuple = AsyncMock(return_value=mock_tuple)
        with pytest.raises(ValueError, match="already concise"):
            await compact_conversation_thread("thread-1", keep_recent_messages=6)


@pytest.mark.asyncio
async def test_compact_conversation_success():
    messages = _make_sample_messages()
    mock_tuple = MagicMock()
    mock_tuple.checkpoint = {"channel_values": {"messages": messages}}

    mock_graph = MagicMock()
    mock_graph.aupdate_state = AsyncMock()

    with patch("agent.modules.conversations.compaction.get_history_checkpointer") as mock_chk, \
         patch("agent.modules.conversations.compaction._generate_context_summary", return_value="Dense summary of tools and discussion"), \
         patch("agent.modules.conversations.compaction.get_workflow_graph", return_value=mock_graph), \
         patch("agent.modules.conversations.compaction.get_thread_messages_payload", return_value=([{"role": "user", "content": "test"}], "chk-new")):

        mock_chk.return_value.aget_tuple = AsyncMock(return_value=mock_tuple)

        result = await compact_conversation_thread("thread-1", keep_recent_messages=4)

        assert result["status"] == "compacted"
        assert result["active_checkpoint_id"] == "chk-new"
        assert result["summary"] == "Dense summary of tools and discussion"
        assert result["kept_count"] >= 2
        assert result["compacted_count"] > 0

        # Verify aupdate_state was called
        mock_graph.aupdate_state.assert_awaited_once()
        call_args = mock_graph.aupdate_state.call_args[0]
        state_values = call_args[1]
        updated_msgs = state_values["messages"]

        # First item must be RemoveMessage with REMOVE_ALL_MESSAGES
        assert isinstance(updated_msgs[0], RemoveMessage)
        assert updated_msgs[0].id == REMOVE_ALL_MESSAGES

        # Followed by Human summary and AI summary
        assert isinstance(updated_msgs[1], HumanMessage)
        assert updated_msgs[1].additional_kwargs.get("is_compact_summary") is True
        assert isinstance(updated_msgs[2], AIMessage)
        assert updated_msgs[2].additional_kwargs.get("is_compact_summary") is True

        # And recent messages start with human
        assert isinstance(updated_msgs[3], HumanMessage)


def test_is_valid_message_sequence():
    # Orphan tool message should be invalid
    orphan = [ToolMessage(content="result", tool_call_id="call-orphan", id="t1")]
    assert is_valid_message_sequence(orphan) is False

    # AIMessage with tool calls followed by ToolMessage should be valid
    valid_chain = [
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "f", "args": {}}], id="a1"),
        ToolMessage(content="result", tool_call_id="call-1", id="t1"),
    ]
    assert is_valid_message_sequence(valid_chain) is True

    # AIMessage followed by orphan ToolMessage (missing call-2) should be invalid
    invalid_chain = [
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "f", "args": {}}], id="a1"),
        ToolMessage(content="result", tool_call_id="call-1", id="t1"),
        ToolMessage(content="result2", tool_call_id="call-2", id="t2"),
    ]
    assert is_valid_message_sequence(invalid_chain) is False


def test_resolve_compaction_cutoff_preserves_human_turn_when_long():
    # Latest turn has 8 tool messages, exceeding target_keep=4
    msgs = [
        HumanMessage(content="Turn 1", id="h1"),
        AIMessage(content="Answer 1", id="a1"),
        HumanMessage(content="Turn 2: Big task", id="h2"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "f", "args": {}}], id="a2"),
        ToolMessage(content="r1", tool_call_id="c1", id="t1"),
        AIMessage(content="", tool_calls=[{"id": "c2", "name": "f", "args": {}}], id="a3"),
        ToolMessage(content="r2", tool_call_id="c2", id="t2"),
        AIMessage(content="", tool_calls=[{"id": "c3", "name": "f", "args": {}}], id="a4"),
        ToolMessage(content="r3", tool_call_id="c3", id="t3"),
        AIMessage(content="All done", id="a5"),
    ]
    cutoff = resolve_compaction_cutoff(msgs, target_keep=4)
    # Must pick index 2 (Turn 2 HumanMessage) to preserve the entire user turn cleanly
    assert cutoff == 2
    assert msgs[cutoff].content == "Turn 2: Big task"


def test_resolve_compaction_cutoff_safe_ai_boundary_for_single_prompt():
    # Only 1 HumanMessage at index 0, followed by many tool calls
    msgs = [
        HumanMessage(content="Do complex job", id="h0"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "f", "args": {}}], id="a1"),
        ToolMessage(content="r1", tool_call_id="c1", id="t1"),
        AIMessage(content="", tool_calls=[{"id": "c2", "name": "f", "args": {}}], id="a2"),
        ToolMessage(content="r2", tool_call_id="c2", id="t2"),
        AIMessage(content="", tool_calls=[{"id": "c3", "name": "f", "args": {}}], id="a3"),
        ToolMessage(content="r3", tool_call_id="c3", id="t3"),
        AIMessage(content="Final result", id="a4"),
    ]
    cutoff = resolve_compaction_cutoff(msgs, target_keep=4)
    assert 0 < cutoff < len(msgs)
    # The retained sequence must be structurally valid
    assert is_valid_message_sequence(msgs[cutoff:]) is True


@pytest.mark.asyncio
async def test_compact_succeeds_when_recent_window_has_no_human_message():
    # Reproduces the user's issue: thread where 6 recent messages have no HumanMessage
    msgs = [
        HumanMessage(content="Initial request", id="h0"),
        AIMessage(content="Reply 1", id="a0"),
        HumanMessage(content="Followup request", id="h1"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "f", "args": {}}], id="a1"),
        ToolMessage(content="r1", tool_call_id="c1", id="t1"),
        AIMessage(content="", tool_calls=[{"id": "c2", "name": "f", "args": {}}], id="a2"),
        ToolMessage(content="r2", tool_call_id="c2", id="t2"),
        AIMessage(content="", tool_calls=[{"id": "c3", "name": "f", "args": {}}], id="a3"),
        ToolMessage(content="r3", tool_call_id="c3", id="t3"),
        AIMessage(content="Final reply", id="a4"),
    ]
    mock_tuple = MagicMock()
    mock_tuple.checkpoint = {"channel_values": {"messages": msgs}}

    mock_graph = MagicMock()
    mock_graph.aupdate_state = AsyncMock()

    with patch("agent.modules.conversations.compaction.get_history_checkpointer") as mock_chk, \
         patch("agent.modules.conversations.compaction._generate_context_summary", return_value="Summary of older items"), \
         patch("agent.modules.conversations.compaction.get_workflow_graph", return_value=mock_graph), \
         patch("agent.modules.conversations.compaction.get_thread_messages_payload", return_value=([{"role": "user", "content": "test"}], "chk-new")):

        mock_chk.return_value.aget_tuple = AsyncMock(return_value=mock_tuple)

        # Standard keep_recent_messages=6 would previously yield [] from trim_messages
        # and throw "Unable to compact conversation further while preserving valid message structure."
        result = await compact_conversation_thread("thread-deep-tools", keep_recent_messages=6)

        assert result["status"] == "compacted"
        assert result["compacted_count"] > 0
        assert result["kept_count"] > 0
        mock_graph.aupdate_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_compact_aborts_when_summary_fails():
    messages = _make_sample_messages()
    mock_tuple = MagicMock()
    mock_tuple.checkpoint = {"channel_values": {"messages": messages}}

    mock_graph = MagicMock()
    mock_graph.aupdate_state = AsyncMock()
    mock_graph.aget_state = AsyncMock(return_value=MagicMock(tasks=[], next=[]))

    with patch("agent.modules.conversations.compaction.get_history_checkpointer") as mock_chk, \
         patch(
             "agent.modules.conversations.compaction._generate_context_summary",
             side_effect=CompactionSummaryError("LLM down"),
         ), \
         patch("agent.modules.conversations.compaction.get_workflow_graph", return_value=mock_graph), \
         patch("agent.modules.conversations.compaction.get_thread_messages_payload") as mock_payload:

        mock_chk.return_value.aget_tuple = AsyncMock(return_value=mock_tuple)

        with pytest.raises(CompactionSummaryError):
            await compact_conversation_thread("thread-summary-fail")

        mock_graph.aupdate_state.assert_not_awaited()
        mock_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_aborts_when_interrupt_pending():
    messages = _make_sample_messages()
    mock_tuple = MagicMock()
    mock_tuple.checkpoint = {"channel_values": {"messages": messages}}

    mock_graph = MagicMock()
    mock_graph.aupdate_state = AsyncMock()

    with patch("agent.modules.conversations.compaction.get_history_checkpointer") as mock_chk, \
         patch(
             "agent.modules.conversations.compaction.has_pending_interrupt",
             return_value=True,
         ), \
         patch("agent.modules.conversations.compaction.get_workflow_graph", return_value=mock_graph):

        mock_chk.return_value.aget_tuple = AsyncMock(return_value=mock_tuple)

        with pytest.raises(CompactionConflictError, match="awaiting user input"):
            await compact_conversation_thread("thread-interrupt")

        mock_graph.aupdate_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_has_pending_interrupt_true_on_task_interrupts():
    fake_task = MagicMock()
    fake_task.interrupts = [{"id": "i1"}]
    fake_state = MagicMock()
    fake_state.tasks = [fake_task]
    fake_state.next = ("tools",)
    mock_graph = MagicMock()
    mock_graph.aget_state = AsyncMock(return_value=fake_state)

    with patch(
        "agent.modules.conversations.compaction.get_workflow_graph",
        return_value=mock_graph,
    ):
        assert await has_pending_interrupt("thread-x") is True


@pytest.mark.asyncio
async def test_has_pending_interrupt_false_when_clear():
    fake_state = MagicMock()
    fake_state.tasks = []
    fake_state.next = ()
    mock_graph = MagicMock()
    mock_graph.aget_state = AsyncMock(return_value=fake_state)

    with patch(
        "agent.modules.conversations.compaction.get_workflow_graph",
        return_value=mock_graph,
    ):
        assert await has_pending_interrupt("thread-x") is False
