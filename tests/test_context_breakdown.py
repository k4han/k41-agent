"""Context composition follows the actual model input and provider totals."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, LLMResult

from agent.modules.usage.context_breakdown import (
    estimate_compacted_context_breakdown,
    estimate_context_breakdown,
    include_response_in_context,
    reconcile_context_breakdown,
)
from agent.modules.usage.tracking import with_usage_tracking
from agent.modules.workflows import model_context, prompt_builders, system_prompt_cache


def test_compacted_breakdown_recounts_history_and_preserves_prompt_estimates():
    previous = {
        "system_prompt": 1000, "system_tools": 800, "skills": 500, "subagents": 300,
        "user_messages": 40000, "agent_responses": 30000, "tool_calls": 20000,
    }
    retained = [
        HumanMessage(content="[Conversation Context Summary] Earlier work"),
        AIMessage(content="Reading the remaining file", tool_calls=[{
            "name": "read", "args": {"path": "file.py"}, "id": "call-1",
        }]),
        ToolMessage(content="Small file content", tool_call_id="call-1"),
    ]
    result = estimate_compacted_context_breakdown(retained, previous)
    history = estimate_context_breakdown(retained)
    for key in ("system_prompt", "system_tools", "skills", "subagents"):
        assert result[key] == previous[key]
    for key in ("user_messages", "agent_responses", "tool_calls"):
        assert 0 < result[key] == history[key] < previous[key]
    assert previous["user_messages"] == 40000


def test_compacted_breakdown_without_previous_usage_counts_absent_categories_as_zero():
    result = estimate_compacted_context_breakdown([HumanMessage(content="Summary")])
    assert result["user_messages"] > 0
    assert all(result[key] == 0 for key in result if key != "user_messages")


def test_prompt_sections_and_tool_history_are_counted_separately():
    sections = {}
    tools = [SimpleNamespace(name=name) for name in ("call_agent", "skill", "read")]
    catalog = SimpleNamespace(
        get_callable_agents=lambda name: ["researcher"],
        get_agent=lambda name: SimpleNamespace(description="Find reliable sources."),
    )
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Follow the user request.", working_dir="/workspace",
        agent_name="default", tools=tools, catalog=catalog,
        skills_catalog_xml="<available_skills><skill>Review code</skill></available_skills>",
        sections=sections,
    )
    sections["active_skills"] = "\n\n<active_skills>Check the behavior before editing.</active_skills>"
    system = SystemMessage(content=prompt + sections["active_skills"])
    user = HumanMessage(content="Inspect the source file")
    assistant = AIMessage(content="I will read it.", tool_calls=[{
        "name": "read", "args": {"path": "source.py"}, "id": "read-1",
    }])
    tool = ToolMessage(content="File contents", tool_call_id="read-1", name="read")
    schemas = [{"name": "read", "parameters": {"path": {"type": "string"}}}]
    estimates = estimate_context_breakdown([system, user, assistant, tool], tools=schemas, prompt_sections=sections)
    assert all(value > 0 for value in estimates.values())
    assert estimates["system_tools"] == count_tokens_approximately([], tools=schemas)
    assert estimates["user_messages"] == count_tokens_approximately([user])
    plain_prompt = system.content
    for section in sections.values():
        plain_prompt = plain_prompt.replace(section, "", 1)
    assert estimates["system_prompt"] == count_tokens_approximately([SystemMessage(content=plain_prompt)])
    plain_response = assistant.model_copy(update={"tool_calls": []})
    assert estimates["agent_responses"] == count_tokens_approximately([plain_response])
    assert estimates["tool_calls"] == (
        count_tokens_approximately([assistant, tool]) - estimates["agent_responses"]
    )
    assert sections["skills"] in prompt


def test_breakdown_retains_only_the_history_sent_to_the_model():
    old = [HumanMessage(content="Old request " * 100), AIMessage(content="Old response " * 100)]
    retained = [SystemMessage(content="Summary of prior context"), HumanMessage(content="Continue")]
    before = estimate_context_breakdown([*old, *retained])
    after = estimate_context_breakdown(retained)
    assert after["user_messages"] < before["user_messages"]
    assert after["agent_responses"] == 0
    assert after["skills"] == after["subagents"] == after["tool_calls"] == 0


def test_reconciled_categories_sum_to_reported_input_even_after_rounding():
    estimates = estimate_context_breakdown([
        SystemMessage(content="Instructions"), HumanMessage(content="Hello"), AIMessage(content="Previous answer"),
    ])
    for tokens in (0, 1, 2, 11, 12345):
        breakdown = reconcile_context_breakdown(estimates, tokens)
        assert sum(breakdown.values()) == tokens
        assert all(type(value) is int and value >= 0 for value in breakdown.values())
        assert breakdown["skills"] == breakdown["subagents"] == breakdown["system_tools"] == 0


@pytest.mark.parametrize("tool_calls", [[], [{
    "name": "read", "args": {"path": "source.py"}, "id": "read-1",
}]])
def test_streamed_and_persisted_breakdowns_match(monkeypatch, tool_calls):
    from agent.modules.usage import tracking

    events, records = [], []
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(tracking, "_schedule_record", records.append)
    estimates = estimate_context_breakdown([SystemMessage(content="Instructions"), HumanMessage(content="Hello")])
    config = with_usage_tracking(
        {"configurable": {"thread_id": "thread-1"}}, agent_name="default",
        provider_name="test", model_name="test-model", context_breakdown=estimates,
    )
    response = AIMessage(
        content="Done", tool_calls=tool_calls,
        usage_metadata={"input_tokens": 123, "output_tokens": 80, "total_tokens": 203},
    )
    config["callbacks"][-1].on_llm_end(LLMResult(generations=[[ChatGeneration(message=response)]]), run_id=uuid4())
    model_context.emit_reported_context_usage(
        response, resolved=SimpleNamespace(provider_name="test", model_name="test-model"),
        config=config, context_breakdown=estimates,
    )
    stored = records[0].usage_metadata
    assert events[0]["context_breakdown"] == include_response_in_context(
        stored["context_breakdown"], records[0].output_tokens, stored["context_output_breakdown"],
    )
    assert events[0]["context_breakdown"]["agent_responses"] > 0
    assert (events[0]["context_breakdown"]["tool_calls"] > 0) == bool(tool_calls)
    assert sum(events[0]["context_breakdown"].values()) == 203
    assert events[0]["current_context_tokens"] == 203
    assert events[0]["input_tokens"] == 123
    assert events[0]["output_tokens"] == records[0].output_tokens == 80


def test_each_call_replaces_the_previous_completed_context(monkeypatch):
    events = []
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    first = AIMessage(content="First response", usage_metadata={
        "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
    })
    history = [SystemMessage(content="Instructions"), HumanMessage(content="Hello")]
    for response in (first, AIMessage(content="Second response", usage_metadata={
        "input_tokens": 150, "output_tokens": 30, "total_tokens": 180,
    })):
        estimates = estimate_context_breakdown(history)
        model_context.emit_reported_context_usage(
            response, resolved=SimpleNamespace(provider_name="test", model_name="test-model"),
            config={}, context_breakdown=estimates,
        )
        assert events[-1]["context_breakdown"]["agent_responses"] == (
            reconcile_context_breakdown(estimates, response.usage_metadata["input_tokens"])["agent_responses"]
            + response.usage_metadata["output_tokens"]
        )
        history.extend([response, HumanMessage(content="Continue")])
    assert [event["current_context_tokens"] for event in events] == [120, 180]


def test_prompt_cache_preserves_catalog_sections_without_shared_mutation(monkeypatch):
    monkeypatch.setattr(system_prompt_cache, "get_system_prompt_cache_ttl_seconds", lambda: 30)
    key = ("context-breakdown-test",)
    sections = {"skills": "Skill catalog", "subagents": "Agent catalog"}
    system_prompt_cache.store_system_prompt(key, "Full prompt", sections=sections)
    sections["skills"] = "Changed"
    restored = {}
    assert system_prompt_cache.get_cached_system_prompt(key, sections=restored) == "Full prompt"
    assert restored["skills"] == "Skill catalog"
    restored["skills"] = "Changed again"
    next_sections = {}
    system_prompt_cache.get_cached_system_prompt(key, sections=next_sections)
    assert next_sections["skills"] == "Skill catalog"
