"""Skill responses survive history reduction without changing prompt prefixes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from agent.modules.conversations import compaction
from agent.modules.skills.context import bind_skill_context, is_skill_instruction, model_skill_history
from agent.modules.tools.runtime.output_retention import retain_message
from agent.modules.usage.context_breakdown import estimate_context_breakdown, estimate_compacted_context_breakdown
from agent.modules.workflows.model_context import prepare_model_context


def activation(name="demo", version="v1", call_id="load-1", content="Keep these exact instructions."):
    return {name: {"name": name, "version": version, "content": content, "context_call_id": call_id}}


def history(active):
    messages = model_skill_history([HumanMessage(content="Load the skill", id="initial")], active)
    messages += [AIMessage(content="Old analysis. " * 2000, id="old-analysis"),
                 HumanMessage(content="Continue", id="recent"), AIMessage(content="Ready", id="ready"),
                 HumanMessage(content="Proceed", id="latest")]
    return messages


@pytest.mark.asyncio
async def test_compaction_keeps_only_current_versions_verbatim_and_is_repeatable(monkeypatch):
    old = activation(content="Obsolete instructions", call_id="old-load")
    active = {**activation(call_id="fresh-load", version="v2"), **activation("second", call_id="second-load")}
    messages = history(old)
    messages = model_skill_history(messages, active)
    summarize = AsyncMock(return_value="Earlier work is complete.")
    monkeypatch.setattr(compaction, "_generate_context_summary", summarize)
    for attempt in range(2):
        if attempt:
            messages += [AIMessage(content="Additional work. " * 2000), HumanMessage(content="Continue", id="next")]
        compacted = await compaction.compact_message_history(messages, active_skills=active,
            keep_recent_messages=2, max_retained_tokens=1000)
        messages = compacted.messages
        assert compaction.is_valid_message_sequence(messages)
        assert sorted(message.content for message in messages if is_skill_instruction(message)) == sorted(
            entry["content"] for entry in active.values())
        assert all("Obsolete instructions" not in str(message.content) for message in messages)
        assert count_tokens_approximately(messages) <= 1000
    for call in summarize.await_args_list:
        assert not any(is_skill_instruction(message) and message.content in {entry["content"] for entry in active.values()}
                       for message in call.args[0])


@pytest.mark.asyncio
async def test_compaction_rebuilds_skill_without_pinning_sibling_tool_output(monkeypatch):
    active = activation()
    skill_result = model_skill_history([HumanMessage(content="Start")], active)[-1]
    messages = [HumanMessage(content="Old request", id="old-user"), AIMessage(content="", tool_calls=[
        {"name": "skill", "args": {"name": "demo"}, "id": "load-1"},
        {"name": "read", "args": {"path": "large.txt"}, "id": "read-1"},
    ]), skill_result, ToolMessage(content="Sibling output " * 4000, tool_call_id="read-1", name="read"),
        HumanMessage(content="Latest request", id="latest")]
    monkeypatch.setattr(compaction, "_generate_context_summary", AsyncMock(return_value="Read completed."))
    result = await compaction.compact_message_history(messages, active_skills=active,
        keep_recent_messages=2, max_retained_tokens=300)
    assert compaction.is_valid_message_sequence(result.messages)
    assert len([message for message in result.messages if is_skill_instruction(message)]) == 1
    assert not any(isinstance(message, ToolMessage) and message.name == "read" for message in result.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("trim", [False, True])
async def test_model_context_keeps_skills_after_automatic_compaction_and_channel_trim(monkeypatch, trim):
    active = activation()
    messages = history(active)
    monkeypatch.setattr(compaction, "_generate_context_summary", AsyncMock(return_value="Work summarized."))
    retained, updates = await prepare_model_context(messages, system=SystemMessage(content="Fixed prompt"),
        context=SimpleNamespace(channel_context_trim_threshold=10 if trim else None),
        agent_config=SimpleNamespace(context_compact_threshold=75),
        resolved=SimpleNamespace(context_window=3000), config={"configurable": {"thread_id": "thread"}},
        active_skills=active)
    assert updates
    assert compaction.is_valid_message_sequence(retained)
    assert [message.content for message in retained if is_skill_instruction(message)] == [active["demo"]["content"]]
    assert any(message.id == "latest" for message in retained)


@pytest.mark.asyncio
async def test_oversized_skill_fails_without_mutating_original_history(monkeypatch):
    active = activation(content="Never truncate this instruction. " * 5000)
    messages = history(active)
    original = list(messages)
    summarize = AsyncMock(return_value="Summary")
    monkeypatch.setattr(compaction, "_generate_context_summary", summarize)
    with pytest.raises(compaction.CompactionBudgetError):
        await prepare_model_context(messages, system=SystemMessage(content="Prompt"),
            context=SimpleNamespace(), agent_config=SimpleNamespace(context_compact_threshold=75),
            resolved=SimpleNamespace(context_window=2000), config={}, active_skills=active)
    assert messages == original
    assert messages[2].content == active["demo"]["content"]
    summarize.assert_not_awaited()


def test_legacy_receipts_restore_pinned_snapshot_and_unload_removes_full_content():
    active = activation()
    active["demo"].pop("context_call_id")
    messages = [HumanMessage(content="Continue", id="user")]
    bound = bind_skill_context(active, messages)
    assert bound == bind_skill_context(active, messages)
    restored = model_skill_history(messages, bound)
    assert model_skill_history(restored, bound) is restored
    unloaded = model_skill_history(restored, {})
    assert not any(is_skill_instruction(message) for message in unloaded)
    assert all(active["demo"]["content"] not in message.content for message in unloaded)
    assert compaction.is_valid_message_sequence(unloaded)


@pytest.mark.asyncio
async def test_large_skill_response_is_exempt_from_output_truncation():
    active = activation(content="Instructions must remain byte-exact.\n" * 3000)
    message = model_skill_history([HumanMessage(content="Load")], active)[-1]
    assert await retain_message(message, SimpleNamespace()) is message


def test_usage_counts_skill_history_once_and_only_reserves_fixed_prompt():
    active = activation()
    messages = model_skill_history([HumanMessage(content="Load")], active)
    fixed = estimate_context_breakdown([SystemMessage(content="Fixed instructions")])
    total = estimate_context_breakdown([SystemMessage(content="Fixed instructions"), *messages])
    estimated = estimate_compacted_context_breakdown(messages, total, context_prompt_breakdown=fixed)
    assert total == estimated
    assert total["skills"] == count_tokens_approximately([messages[-1]])
    assert sum(total.values()) == count_tokens_approximately([SystemMessage(content="Fixed instructions"), *messages])
