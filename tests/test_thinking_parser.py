"""Tests for the thinking-aware content parsing utilities."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from tests.stream_fixtures import EventGraph

from agent.modules.agent_runtime.runner import (
    _StreamingChunkExtractor,
    run_agent_stream,
)
from agent.shared.infrastructure.parsing import (
    extract_final_text_content,
    extract_thinking_content,
)
from agent.shared.infrastructure.thinking_parser import (
    ContentWithThinking,
    ThinkingTagStreamParser,
    extract_thinking_from_text,
    parse_content_for_thinking,
    strip_thinking_tags,
)


# ---------------------------------------------------------------------------
# Inline tag reasoning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello world", "Hello world"),
        ("<thinking>hidden</thinking>", ""),
        ("Hi <thinking>re</thinking> bye", "Hi  bye"),
        (
            "Plan: <thinking>plan A\nplan B\n</thinking> result",
            "Plan:  result",
        ),
    ],
)
def test_strip_thinking_tags_removes_inline_reasoning(text: str, expected: str) -> None:
    assert strip_thinking_tags(text) == expected


def test_strip_thinking_tags_returns_unchanged_when_no_tag() -> None:
    assert strip_thinking_tags("Just a plain answer") == "Just a plain answer"


def test_strip_thinking_tags_handles_aliases() -> None:
    assert strip_thinking_tags("Yes <reason>because</reason>") == "Yes "


@pytest.mark.parametrize(
    "tagged_sample, expected_text, expected_thinking",
    [
        (
            "visible <Thought>inner</Thought> visible",
            "visible  visible",
            "inner",
        ),
        (
            "visible <REFLECTION>r</REFLECTION> visible",
            "visible  visible",
            "r",
        ),
        (
            "visible <INNER_MONOLOGUE>guess</INNER_MONOLOGUE> visible",
            "visible  visible",
            "guess",
        ),
        (
            "ok <plan>first step</plan> ok",
            "ok  ok",
            "first step",
        ),
    ],
)
def test_strip_thinking_tags_handles_case_and_compound_aliases(
    tagged_sample: str, expected_text: str, expected_thinking: str
) -> None:
    assert strip_thinking_tags(tagged_sample) == expected_text
    assert extract_thinking_from_text(tagged_sample) == expected_thinking


@pytest.mark.parametrize(
    ("alias", "open_tag", "close_tag"),
    [
        ("think", "think", "think"),
        ("thought", "thought", "thought"),
        ("thoughts", "thoughts", "thoughts"),
        ("reflection", "reflection", "reflection"),
        ("reflect", "reflect", "reflect"),
        ("inner_monologue", "inner_monologue", "inner_monologue"),
        ("plan", "plan", "plan"),
    ],
)
def test_strip_thinking_tags_handles_each_alias(
    alias: str, open_tag: str, close_tag: str
) -> None:
    assert (
        strip_thinking_tags(f"hello <{open_tag}>secret</{close_tag}> world")
        == "hello  world"
    )
    assert extract_thinking_from_text(
        f"hello <{open_tag}>secret</{close_tag}> world"
    ) == "secret"


def test_strip_thinking_tags_strips_qwen3_style_think() -> None:
    raw = "<think>answer is 4</think> 42 4"
    assert strip_thinking_tags(raw) == " 42 4"
    assert extract_thinking_from_text(raw) == "answer is 4"


def test_strip_thinking_tags_keeps_partial_tag_at_end_untouched() -> None:
    # `partial=False` (full-text scan) should emit the remainder as visible
    # text so the caller never silently loses characters.
    assert strip_thinking_tags("hello <thinking>x") == "hello <thinking>x"


# ---------------------------------------------------------------------------
# Structured parts vs inline tags
# ---------------------------------------------------------------------------


def test_parse_structured_thinking_part() -> None:
    result = parse_content_for_thinking(
        {"type": "thinking", "thinking": "internal reasoning"}
    )
    assert result == ContentWithThinking(text="", thinking="internal reasoning")


def test_parse_structured_thinking_list() -> None:
    result = parse_content_for_thinking(
        [
            {"type": "thinking", "thinking": "plan"},
            {"type": "text", "text": "4"},
        ]
    )
    assert result.text == "4"
    assert result.thinking == "plan"


def test_parse_inline_thinking_tags_inside_text_part() -> None:
    result = parse_content_for_thinking(
        [{"type": "text", "text": "Answer <thinking>hmm</thinking> final"}]
    )
    assert result.text == "Answer  final"
    assert result.thinking == "hmm"


def test_parse_mixed_inline_and_structured_thinking() -> None:
    result = parse_content_for_thinking(
        [
            {"type": "thinking", "thinking": "from-struct"},
            {"type": "text", "text": "body <thinking>inline</thinking> tail"},
        ]
    )
    assert result.text == "body  tail"
    assert result.thinking == "from-struct\ninline"


def test_parse_reasoning_alias_and_reasoning_content() -> None:
    result = parse_content_for_thinking(
        [
            {"type": "reasoning", "reasoning": "r1"},
            {"type": "reasoning_content", "reasoning_content": "r2"},
        ]
    )
    assert result.thinking == "r1\nr2"


def test_parse_none_returns_empty() -> None:
    result = parse_content_for_thinking(None)
    assert result.text == ""
    assert result.thinking == ""


# ---------------------------------------------------------------------------
# Streaming parser: state across chunks
# ---------------------------------------------------------------------------


def test_streaming_parser_emits_full_visible_after_complete_tag() -> None:
    parser = ThinkingTagStreamParser()
    first = parser.feed("Hello <think")
    second = parser.feed("ing>think step</thinking> world")
    flush = parser.flush()

    assert first.text == "Hello "
    assert first.thinking == ""
    assert second.text == " world"
    assert second.thinking == "think step"
    assert flush.text == ""
    assert flush.thinking == ""


def test_streaming_parser_carries_trailing_partial_open_tag() -> None:
    parser = ThinkingTagStreamParser()
    first = parser.feed("Hello <think")
    second = parser.feed("ing>")
    third = parser.feed("reason")
    last = parser.feed("</thinking> done")

    combined_thinking = "".join(
        piece.thinking for piece in (first, second, third, last)
    )
    combined_text = "".join(piece.text for piece in (first, second, third, last))

    assert "reason" in combined_thinking
    assert "done" in combined_text or combined_text.endswith("done")


def test_streaming_parser_handles_close_tag_split() -> None:
    parser = ThinkingTagStreamParser()
    parser.feed("<thinking>think step")
    first = parser.feed("</think")
    second = parser.feed("ing> rest")
    flush = parser.flush()

    combined_text = first.text + second.text + flush.text
    combined_thinking = first.thinking + second.thinking + flush.thinking

    assert "think step" in combined_thinking
    assert "rest" in combined_text


def test_streaming_parser_flush_drains_unclosed_thinking_as_thinking() -> None:
    parser = ThinkingTagStreamParser()
    parser.feed("<thinking>lone thought")
    flush = parser.flush()
    assert flush.text == ""
    assert flush.thinking == "lone thought"


def test_streaming_parser_flush_drains_unconfirmed_partial_tag_as_visible() -> None:
    parser = ThinkingTagStreamParser()
    first = parser.feed("question <th")
    flush = parser.flush()
    assert first.text == "question "
    assert flush.text == "<th"
    assert flush.thinking == ""


@pytest.mark.parametrize(
    ("open_fragment", "rest", "expected_thinking"),
    [
        ("hello <th", "ink>secret</thinking> world", "secret"),
        ("hello <TH", "OUGHT>secret</thought> world", "secret"),
        ("hello <refl", "ection>r</reflection> world", "r"),
        ("hello <inner", "_monologue>guess</inner_monologue> world", "guess"),
    ],
)
def test_streaming_parser_partitions_each_alias_fragmented_across_chunks(
    open_fragment: str, rest: str, expected_thinking: str
) -> None:
    parser = ThinkingTagStreamParser()
    parser.feed(open_fragment)
    second = parser.feed(rest)
    flush = parser.flush()
    combined_text = second.text + flush.text
    combined_thinking = second.thinking + flush.thinking
    assert expected_thinking in combined_thinking
    assert "world" in combined_text


# ---------------------------------------------------------------------------
# Compatibility shims in shared.infrastructure.parsing
# ---------------------------------------------------------------------------


def test_extract_final_text_strips_inline_thinking_tag() -> None:
    assert extract_final_text_content("<thinking>internal</thinking>visible") == "visible"


def test_extract_final_text_keeps_structured_visible_part() -> None:
    message = AIMessage(
        content=[
            {"type": "thinking", "thinking": "hidden"},
            {"type": "text", "text": "shown"},
        ]
    )
    assert extract_final_text_content(message.content) == "shown"


def test_extract_thinking_content_returns_structured_thinking() -> None:
    message = AIMessage(
        content=[
            {"type": "thinking", "thinking": "thought"},
            {"type": "text", "text": "visible"},
        ]
    )
    assert extract_thinking_content(message.content) == "thought"


def test_extract_thinking_content_returns_inline_tag_thinking() -> None:
    assert extract_thinking_content("hello <thinking>secret</thinking> world") == "secret"


# ---------------------------------------------------------------------------
# Streaming extractor in agent_runtime.runner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_extractor_splits_structured_thinking_and_text() -> None:
    extractor = _StreamingChunkExtractor()
    chunk = AIMessageChunk(
        content=[
            {"type": "thinking", "thinking": "compute"},
            {"type": "text", "text": "4"},
        ]
    )
    delta = extractor.extract((chunk, {"langgraph_node": "llm"}))
    assert delta.text == "4"
    assert delta.thinking == "compute"


@pytest.mark.asyncio
async def test_streaming_extractor_handles_inline_tags_within_string() -> None:
    extractor = _StreamingChunkExtractor()
    chunk = AIMessageChunk(content="A <thinking>plan</thinking> B")
    delta = extractor.extract((chunk, {}))
    assert delta.text == "A  B"
    assert delta.thinking == "plan"


@pytest.mark.asyncio
async def test_streaming_extractor_carries_partial_open_tag() -> None:
    extractor = _StreamingChunkExtractor()
    first = extractor.extract((AIMessageChunk(content="hi <think"), {}))
    second = extractor.extract((AIMessageChunk(content="ing>step</thinking> done"), {}))
    flush = extractor.flush()

    combined_text = first.text + second.text + flush.text
    combined_thinking = first.thinking + second.thinking + flush.thinking

    assert "hi " in combined_text
    assert "done" in combined_text
    assert "step" in combined_thinking


# ---------------------------------------------------------------------------
# End-to-end runner: events emitted for thinking should be optional
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_stream_strips_inline_thinking_from_message_chunks(
    monkeypatch,
) -> None:
    class _FakeCatalog:
        def get_agent(self, name: str):
            from types import SimpleNamespace

            return SimpleNamespace(
                graph_type="react_agent",
                context_compact_threshold=75,
                tools=[],
            )

    class _FakeGraph(EventGraph):
        async def events(self, payload, **kwargs):
            yield ("messages", (AIMessageChunk(content="Plan "), {}))
            yield ("messages", (AIMessageChunk(content="<thinking>"), {}))
            yield ("messages", (AIMessageChunk(content="internal"), {}))
            yield ("messages", (AIMessageChunk(content="</thinking> visible"), {}))
            yield (
                "values",
                {
                    "messages": [
                        AIMessage(
                            content=(
                                "Plan "
                                "<thinking>internal</thinking>"
                                " visible"
                            ),
                            id="msg-1",
                        )
                    ]
                },
            )

    from agent.modules.agent_runtime import runner as runner_module

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(runner_module, "get_workflow_graph", lambda name: _FakeGraph())
    monkeypatch.setattr(runner_module, "make_run_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        runner_module,
        "make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    events = [
        event
        async for event in runner_module.run_agent_stream(
            user_input="go",
            thread_id="thread-think-1",
            agent_name="default",
            emit_thinking=False,
        )
    ]

    visible_events = [event for event in events if event.get("type") == "message"]
    thinking_events = [event for event in events if event.get("type") == "thinking"]
    final_events = [event for event in events if event.get("type") == "final"]

    combined_visible = "".join(event["content"] for event in visible_events)
    assert "<thinking" not in combined_visible
    assert "internal" not in combined_visible

    assert not thinking_events, (
        "Thinking events should not be emitted when emit_thinking=False"
    )

    assert final_events, "Final event should still be emitted"
    assert "<thinking" not in final_events[-1]["content"]
    assert "internal" not in final_events[-1]["content"]


@pytest.mark.asyncio
async def test_run_agent_stream_emits_thinking_events_when_enabled(
    monkeypatch,
) -> None:
    class _FakeCatalog:
        def get_agent(self, name: str):
            from types import SimpleNamespace

            return SimpleNamespace(
                graph_type="react_agent",
                context_compact_threshold=75,
                tools=[],
            )

    class _FakeGraph(EventGraph):
        async def events(self, payload, **kwargs):
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {"type": "thinking", "thinking": "compute 2+2"},
                            {"type": "text", "text": "two"},
                        ]
                    ),
                    {"langgraph_node": "llm"},
                ),
            )
            yield (
                "messages",
                (AIMessageChunk(content=" plus two"), {"langgraph_node": "llm"}),
            )
            yield (
                "values",
                {
                    "messages": [
                        AIMessage(
                            content=[
                                {"type": "thinking", "thinking": "compute 2+2"},
                                {"type": "text", "text": "two plus two"},
                            ],
                            id="msg-think-2",
                        )
                    ]
                },
            )

    from agent.modules.agent_runtime import runner as runner_module

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(runner_module, "get_workflow_graph", lambda name: _FakeGraph())
    monkeypatch.setattr(runner_module, "make_run_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        runner_module,
        "make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    events = [
        event
        async for event in runner_module.run_agent_stream(
            user_input="go",
            thread_id="thread-think-2",
            agent_name="default",
            emit_thinking=True,
        )
    ]

    thinking_events = [event for event in events if event.get("type") == "thinking"]
    final_events = [event for event in events if event.get("type") == "final"]
    message_events = [event for event in events if event.get("type") == "message"]

    assert thinking_events
    assert "compute 2+2" in "".join(event["content"] for event in thinking_events)
    assert final_events
    assert final_events[-1]["content"] == "two plus two"
    assert "".join(event["content"] for event in message_events) == "two plus two"


# ---------------------------------------------------------------------------
# OpenAI Responses reasoning summaries
# ---------------------------------------------------------------------------


def test_parse_content_reads_reasoning_summary_parts() -> None:
    content = [
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [
                {"index": 0, "type": "summary_text", "text": "First idea."},
                {"index": 1, "type": "summary_text", "text": "Second idea."},
            ],
        },
        {"type": "text", "text": "Answer"},
    ]

    assert extract_thinking_content(content) == "First idea.\n\nSecond idea."
    assert extract_final_text_content(content) == "Answer"


def test_streaming_extractor_reads_reasoning_summary_deltas() -> None:
    extractor = _StreamingChunkExtractor()

    def summary_chunk(index: int, text: str) -> tuple[AIMessageChunk, dict]:
        return (
            AIMessageChunk(
                content=[
                    {
                        "type": "reasoning",
                        "index": 0,
                        "summary": [{"index": index, "type": "summary_text", "text": text}],
                    }
                ]
            ),
            {"langgraph_node": "llm"},
        )

    deltas = [
        extractor.extract(summary_chunk(0, "")),
        extractor.extract(summary_chunk(0, "First")),
        extractor.extract(summary_chunk(1, "")),
        extractor.extract(summary_chunk(1, "Second")),
    ]

    assert all(not delta.text for delta in deltas)
    assert "".join(delta.thinking for delta in deltas) == "First\n\nSecond"


# ---------------------------------------------------------------------------
# Thinking emission order and de-duplication
# ---------------------------------------------------------------------------


async def _collect_stream_events(monkeypatch, graph_events, thread_id: str) -> list[dict]:
    class _FakeCatalog:
        def get_agent(self, name: str):
            from types import SimpleNamespace

            return SimpleNamespace(
                graph_type="react_agent",
                context_compact_threshold=75,
                tools=[],
            )

    class _FakeGraph(EventGraph):
        async def events(self, payload, **kwargs):
            for event in graph_events:
                yield event

    from agent.modules.agent_runtime import runner as runner_module

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(runner_module, "get_workflow_graph", lambda name: _FakeGraph())
    monkeypatch.setattr(runner_module, "make_run_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        runner_module,
        "make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )
    return [
        event
        async for event in runner_module.run_agent_stream(
            user_input="go",
            thread_id=thread_id,
            agent_name="default",
            emit_thinking=True,
        )
    ]


@pytest.mark.asyncio
async def test_run_agent_stream_does_not_repeat_streamed_thinking(monkeypatch) -> None:
    events = await _collect_stream_events(
        monkeypatch,
        [
            (
                "messages",
                (
                    AIMessageChunk(content=[{"type": "thinking", "thinking": "plan"}]),
                    {"langgraph_node": "llm"},
                ),
            ),
            ("messages", (AIMessageChunk(content="done"), {"langgraph_node": "llm"})),
            (
                "values",
                {
                    "messages": [
                        AIMessage(
                            content=[
                                {"type": "thinking", "thinking": "plan"},
                                {"type": "text", "text": "done"},
                            ],
                            id="msg-dedupe",
                        )
                    ]
                },
            ),
        ],
        "thread-think-dedupe",
    )

    thinking_events = [event for event in events if event.get("type") == "thinking"]
    assert [event["content"] for event in thinking_events] == ["plan"]


@pytest.mark.asyncio
async def test_run_agent_stream_falls_back_to_values_thinking_before_final(monkeypatch) -> None:
    events = await _collect_stream_events(
        monkeypatch,
        [
            (
                "values",
                {
                    "messages": [
                        AIMessage(
                            content=[
                                {"type": "thinking", "thinking": "unstreamed plan"},
                                {"type": "text", "text": "answer"},
                            ],
                            id="msg-fallback",
                        )
                    ]
                },
            ),
        ],
        "thread-think-fallback",
    )

    types = [event.get("type") for event in events if event.get("type") in {"thinking", "final"}]
    assert types == ["thinking", "final"]
    thinking_event = next(event for event in events if event.get("type") == "thinking")
    assert thinking_event["content"] == "unstreamed plan"


@pytest.mark.asyncio
async def test_run_agent_stream_orders_thinking_before_text_in_one_chunk(monkeypatch) -> None:
    """A tool-free turn whose first chunk carries both reasoning and answer text."""
    events = await _collect_stream_events(
        monkeypatch,
        [
            (
                "messages",
                (
                    AIMessageChunk(content="<think>Greet back.</think>Hello"),
                    {"langgraph_node": "llm"},
                ),
            ),
            ("messages", (AIMessageChunk(content=" there!"), {"langgraph_node": "llm"})),
            (
                "values",
                {
                    "messages": [
                        AIMessage(
                            content="<think>Greet back.</think>Hello there!",
                            id="msg-no-tools",
                        )
                    ]
                },
            ),
        ],
        "thread-think-no-tools",
    )

    streamed = [
        (event["type"], event["content"])
        for event in events
        if event.get("type") in {"thinking", "message", "final"}
    ]
    assert streamed == [
        ("thinking", "Greet back."),
        ("message", "Hello"),
        ("message", " there!"),
        ("final", "Hello there!"),
    ]
