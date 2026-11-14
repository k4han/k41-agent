"""Tests for decision-guided context curation workflow graph."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from agent.modules.workflows.constants import DECISION_AGENT_GRAPH_TYPE
from agent.modules.workflows.graphs.decision_agent import (
    _should_continue,
    build_decision_agent_graph,
)
from agent.modules.workflows.nodes.decision import (
    CONTEXT_MARKER,
    FileCandidateSelection,
    FileInclusionDecision,
    FileInclusionBatch,
    FileInclusionEntry,
    build_decision_context,
    decision_node,
    generate_directory_tree,
    read_file_safe,
)
from agent.modules.workflows.registry import GraphRegistry
from agent.modules.workflows.run_config import WorkflowContext, make_config, make_context


class _FakeDecisionModel:
    """Mock model providing structured output or raw text."""

    def __init__(
        self,
        *,
        candidate_selection: FileCandidateSelection | None = None,
        inclusion_decision: FileInclusionDecision | None = None,
    ) -> None:
        self.candidate_selection = candidate_selection or FileCandidateSelection(candidate_files=[])
        self.inclusion_decision = inclusion_decision or FileInclusionDecision(should_include=True)
        self.invocations: list[dict[str, Any]] = []

    def with_structured_output(self, schema: type):
        async def _ainvoke(messages, **kwargs):
            self.invocations.append({"schema": schema, "messages": messages, "kwargs": kwargs})
            if schema == FileCandidateSelection:
                return self.candidate_selection
            if schema == FileInclusionBatch:
                files = json.loads(messages[-1].content)["files"]
                return FileInclusionBatch(files=[FileInclusionEntry(
                    path=item["path"], **self.inclusion_decision.model_dump(),
                ) for item in files])
            raise ValueError(f"Unknown schema: {schema}")

        return SimpleNamespace(ainvoke=_ainvoke)

    async def ainvoke(self, messages, **kwargs):
        self.invocations.append({"messages": messages, "kwargs": kwargs})
        return AIMessage(content="Mock plain response")


def _fake_resolved(model: Any) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        provider_name="test-provider",
        provider_type="mock",
        model_name="test-model",
    )


def test_generate_directory_tree(tmp_path: Path):
    """Verify tree output correctly reflects folder structure and ignores unwanted dirs."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "src" / "utils.py").write_text("# utils", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.pyc").write_text("bin", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "README.md").write_text("# Readme", encoding="utf-8")

    tree = generate_directory_tree(tmp_path)
    assert "src/" in tree
    assert "main.py" in tree
    assert "utils.py" in tree
    assert "README.md" in tree
    assert "__pycache__" not in tree
    assert ".git" not in tree


def test_generate_directory_tree_nonexistent():
    tree = generate_directory_tree("nonexistent/dir/path/xyz")
    assert "Directory not found" in tree


def test_read_file_safe(tmp_path: Path):
    """Verify safe reading of files within the workspace root."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world content", encoding="utf-8")

    # Valid read
    content = read_file_safe(tmp_path, "test.txt")
    assert content == "hello world content"

    # Traversal attempt outside root
    assert read_file_safe(tmp_path, "../outside.txt") is None

    # Binary file detection
    bin_file = tmp_path / "binary.bin"
    bin_file.write_bytes(b"\x00\x01\x02")
    assert read_file_safe(tmp_path, "binary.bin") is None

    # Truncation of large content
    large_file = tmp_path / "large.txt"
    large_file.write_text("a" * 500, encoding="utf-8")
    truncated = read_file_safe(tmp_path, "large.txt", max_bytes=100)
    assert truncated is not None
    assert len(truncated) < 200
    assert "...[content truncated]" in truncated


@pytest.mark.asyncio
async def test_decision_node_keeps_file_bodies_out_of_state(tmp_path: Path, monkeypatch):
    """Verify references hydrate context without persisting enriched messages."""
    # Setup test workspace
    (tmp_path / "agent.py").write_text("class Agent: pass", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("unrelated text", encoding="utf-8")

    fake_model = _FakeDecisionModel(
        candidate_selection=FileCandidateSelection(
            candidate_files=["agent.py"],
            reasoning="Contains agent class definition",
        ),
        inclusion_decision=FileInclusionDecision(
            should_include=True,
            reasoning="Essential Agent definition",
            use_full_content=True,
        ),
    )

    monkeypatch.setattr(
        "agent.modules.workflows.nodes.decision.get_resolved_chat_model",
        lambda **kwargs: _fake_resolved(fake_model),
    )

    context = make_context(
        working_dir=str(tmp_path),
        agent_name="default",
    )
    runtime = SimpleNamespace(context=context)

    initial_msg = HumanMessage(content="How is the agent defined?", id="msg-user-1")
    state = {"messages": [initial_msg]}
    config = make_config("test-thread-1")

    result = await decision_node(state, config, runtime=runtime)

    assert "messages" in result
    assert len(result["messages"]) == 1
    assert result["messages"][0] == initial_msg
    hydrated = await build_decision_context({**state, **result}, str(tmp_path))
    assert CONTEXT_MARKER in hydrated
    assert "<directory_tree>" in hydrated
    assert "agent.py" in hydrated
    assert "class Agent: pass" in hydrated
    assert "class Agent: pass" not in str(result)
    assert result["curated_context"] is None
    assert [call["schema"] for call in fake_model.invocations] == [FileCandidateSelection, FileInclusionBatch]

    # Structured metadata in state updates
    assert result["candidate_files"] == ["agent.py"]
    assert len(result["included_files"]) == 1
    assert result["included_files"][0]["path"] == "agent.py"


@pytest.mark.asyncio
async def test_decision_node_skips_when_already_prepared(tmp_path: Path):
    """Avoid redundant context injection if context marker is already present."""
    already_prepared = HumanMessage(
        content=f"Original question\n\n{CONTEXT_MARKER}\n<directory_tree></directory_tree></workspace_context>",
        id="msg-1",
    )
    state = {"messages": [already_prepared]}
    config = make_config("test-thread-1")

    result = await decision_node(state, config, runtime=None)
    assert result == {}


@pytest.mark.asyncio
async def test_decision_batches_candidates_and_bounds_previews(tmp_path, monkeypatch):
    from agent.modules.workflows.nodes import decision as decision_module

    paths = [f"file_{index}.txt" for index in range(6)]
    for path in paths:
        (tmp_path / path).write_text("raw-file-body-" * 5_000, encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\0\1\2")
    previews = []

    class Model(_FakeDecisionModel):
        def with_structured_output(self, schema):
            if schema != FileInclusionBatch:
                return super().with_structured_output(schema)

            async def invoke(messages, **kwargs):
                self.invocations.append({"schema": schema})
                previews.extend(json.loads(messages[-1].content)["files"])
                return FileInclusionBatch(files=[
                    FileInclusionEntry(path=paths[0], should_include=True, use_full_content=False,
                                       relevant_summary_or_excerpt="focused excerpt " * 1_000),
                    FileInclusionEntry(path=paths[1], should_include=False),
                    FileInclusionEntry(path="not-a-candidate.txt", should_include=True),
                ])

            return SimpleNamespace(ainvoke=invoke)

    model = Model(candidate_selection=FileCandidateSelection(candidate_files=[
        paths[0], paths[0], *paths[1:], "binary.bin", "../outside.txt",
    ]))
    monkeypatch.setattr(decision_module, "get_resolved_chat_model", lambda **kwargs: _fake_resolved(model))
    user = HumanMessage(content="inspect files", id="batch-user")
    result = await decision_node({"messages": [user]}, {
        "configurable": {"working_dir": str(tmp_path), "decision_max_candidates": 8},
    })
    assert len(model.invocations) == 2
    assert [item["path"] for item in previews] == paths
    assert sum(len(item["preview"]) for item in previews) <= decision_module.MAX_INCLUSION_PREVIEW_CHARS
    assert [item["path"] for item in result["included_files"]] == [paths[0], *paths[2:]]
    assert len(result["included_files"][0]["excerpt"]) == decision_module.MAX_EXCERPT_CHARS
    assert "raw-file-body-" not in str(result)
    assert await decision_node(result, {}) == {}

    hydrated = await build_decision_context(result, str(tmp_path))
    assert "focused excerpt" in hydrated
    assert f'<file path="{paths[1]}">' not in hydrated
    assert "not-a-candidate.txt" not in hydrated
    # References read the current workspace and tolerate files removed after selection.
    (tmp_path / paths[2]).write_text("updated file contents", encoding="utf-8")
    (tmp_path / paths[3]).unlink()
    hydrated = await build_decision_context(result, str(tmp_path))
    assert "updated file contents" in hydrated
    assert f'<file path="{paths[3]}">' not in hydrated
    assert await build_decision_context({**result, "messages": [HumanMessage(content="next", id="new-turn")]}, str(tmp_path)) == ""


@pytest.mark.asyncio
async def test_batch_inclusion_keeps_json_fallback_and_default_inclusion(monkeypatch):
    from agent.modules.workflows.nodes.decision import _decide_file_inclusions_llm

    class Model:
        def with_structured_output(self, schema):
            raise NotImplementedError("Structured output unavailable")

        async def ainvoke(self, messages, **kwargs):
            return AIMessage(content='{"files": [{"path": "keep.py", "should_include": true}, '
                             '{"path": "drop.py", "should_include": false}]}')

    kwargs = dict(llm=Model(), user_input="inspect", files={"keep.py": "code", "drop.py": "unrelated"},
                  caller_agent_name="default", provider_name="test", model_name="test", model_kwargs={},
                  system_prompt="Evaluate context")
    result = await _decide_file_inclusions_llm(**kwargs)
    assert result["keep.py"].should_include
    assert not result["drop.py"].should_include

    async def fail(*args, **kwargs):
        raise OSError("Model unavailable")

    kwargs["llm"].ainvoke = fail
    assert await _decide_file_inclusions_llm(**kwargs) == {}


def test_should_continue_evaluates_tool_calls():
    """Verify routing logic checks for tool calls in the last AIMessage."""
    ai_without_tools = AIMessage(content="Final answer", tool_calls=[])
    assert _should_continue({"messages": [ai_without_tools]}) == "__end__"

    ai_with_tools = AIMessage(
        content="Calling tool",
        tool_calls=[{"name": "read_file", "args": {}, "id": "call-1"}],
    )
    assert _should_continue({"messages": [ai_with_tools]}) == "tool"


def test_build_decision_agent_graph_registration(monkeypatch):
    """Verify build_decision_agent_graph registers the graph in GraphRegistry."""
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())

    build_decision_agent_graph(graph_name=DECISION_AGENT_GRAPH_TYPE)

    assert GraphRegistry.is_registered(DECISION_AGENT_GRAPH_TYPE)
    graph = GraphRegistry.get(DECISION_AGENT_GRAPH_TYPE)
    assert graph is not None

    nodes = graph.nodes
    assert "prepare_context" in nodes
    assert "decision" in nodes
    assert "llm" in nodes
    assert "tool" in nodes


@pytest.mark.asyncio
async def test_decision_graph_full_flow(tmp_path: Path, monkeypatch):
    """End-to-end execution of decision graph from user input through decision and llm nodes."""
    (tmp_path / "math_utils.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    # Fake decision model
    decision_model = _FakeDecisionModel(
        candidate_selection=FileCandidateSelection(
            candidate_files=["math_utils.py"],
            reasoning="Math functions here",
        ),
        inclusion_decision=FileInclusionDecision(
            should_include=True,
            reasoning="Includes add function",
            use_full_content=True,
        ),
    )

    # Fake LLM node model: calls a tool, then finishes
    call_count = 0
    model_inputs = []

    class _FakeMainLLM:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages, **kwargs):
            nonlocal call_count
            model_inputs.append(messages)
            call_count += 1
            if call_count == 1:
                return AIMessage(
                    content="Checking file",
                    tool_calls=[{
                        "name": "mock_tool",
                        "args": {"query": "test"},
                        "id": "tool-call-1",
                    }],
                )
            return AIMessage(content="Result is 42", tool_calls=[])

    @tool
    def mock_tool(query: str) -> str:
        """Mock test tool."""
        return f"Tool result for {query}"

    from agent.modules.agents.models import AgentConfig

    default_agent = AgentConfig(
        name="default",
        display_name="kaka",
        description="test",
        graph_type="decision_agent",
        provider="Google",
        model="test-model",
        tools=["mock_tool"],
        context_compact_threshold=75,
        system_prompt="You are a test assistant.",
    )

    class _Catalog:
        def get_agent(self, name: str):
            return default_agent

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _Catalog(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.nodes.decision.get_resolved_chat_model",
        lambda **kwargs: _fake_resolved(decision_model),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.nodes.llm.get_resolved_chat_model",
        lambda **kwargs: _fake_resolved(_FakeMainLLM()),
    )
    async def _mock_aresolve(self, agent_name, **kwargs):
        return [mock_tool]

    monkeypatch.setattr(
        "agent.modules.workflows.nodes.llm.ToolResolver.aresolve_for_agent",
        _mock_aresolve,
    )
    monkeypatch.setattr(
        "agent.modules.workflows.nodes.tool.ToolResolver.aresolve_for_agent",
        _mock_aresolve,
    )

    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())

    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    build_decision_agent_graph(checkpointer=saver, graph_name="test_decision_graph")
    compiled = GraphRegistry.get("test_decision_graph")

    context = make_context(
        working_dir=str(tmp_path),
        agent_name="default",
    )
    config = {
        **make_config("thread-full-test"),
        "configurable": {
            "thread_id": "thread-full-test",
            "working_dir": str(tmp_path),
        },
    }

    initial_input = {"messages": [HumanMessage(content="Calculate 20 + 22", id="msg-input")]}

    # Pass context via ainvoke context argument
    result = await compiled.ainvoke(initial_input, config=config, context=context)

    # Verify flow ran: decision enriched message -> llm called tool -> tool ran -> llm final
    messages = result["messages"]
    assert any(isinstance(m, ToolMessage) for m in messages)
    final_message = messages[-1]
    assert isinstance(final_message, AIMessage)
    assert final_message.content == "Result is 42"

    # The main model sees context on each pass, while checkpoints keep references.
    first_human = next(m for m in messages if isinstance(m, HumanMessage))
    assert first_human.content == "Calculate 20 + 22"
    assert len(model_inputs) == 2
    assert all("def add(a, b)" in items[0].content for items in model_inputs)
    assert "content" not in result["included_files"][0]
    async for checkpoint in saver.alist(config):
        assert "def add(a, b)" not in str(checkpoint.checkpoint)


@pytest.mark.asyncio
async def test_decision_node_with_cloudflare_clef_client(tmp_path: Path):
    """Verify decision_node uses Cloudflare Clef DecisionClient when configured."""
    (tmp_path / "service.py").write_text("class MyService:\n    pass\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("random note\n", encoding="utf-8")

    from agent.modules.decisions import ChoiceAnswer, NoulAnswer
    from agent.modules.workflows.nodes.decision import set_default_decision_client

    class _MockClefClient:
        def __init__(self):
            self.choice_calls = []
            self.noul_calls = []

        async def evaluate_choice(self, state, instructions, criteria, **kwargs):
            self.choice_calls.append({"state": state, "criteria": criteria})
            return ChoiceAnswer(
                choice="service.py",
                probabilities={"service.py": 0.88, "notes.txt": 0.12},
            )

        async def evaluate_noul(self, state, instructions, threshold=0.5, **kwargs):
            self.noul_calls.append({"state": state, "instructions": instructions})
            is_service = "service.py" in state
            return NoulAnswer(
                noul=0.9 if is_service else 0.1,
                decision=is_service,
                confidence=0.9 if is_service else 0.1,
            )

    mock_client = _MockClefClient()
    set_default_decision_client(mock_client)

    try:
        context = make_context(
            working_dir=str(tmp_path),
            agent_name="default",
        )
        runtime = SimpleNamespace(context=context)

        initial_msg = HumanMessage(content="Explain MyService", id="user-msg-cf")
        state = {"messages": [initial_msg]}
        config = make_config("cf-thread")

        result = await decision_node(state, config, runtime=runtime)

        assert len(mock_client.choice_calls) == 1
        assert len(mock_client.noul_calls) == 2
        assert result["decision_source"] == "cloudflare"
        assert result["candidate_files"] == ["service.py", "notes.txt"]
        assert len(result["included_files"]) == 1
        assert result["included_files"][0]["path"] == "service.py"
        assert result["messages"][0].content == initial_msg.content
        assert "class MyService" in await build_decision_context(result, str(tmp_path))
        assert "Cloudflare Clef decision" in result["included_files"][0]["reasoning"]
    finally:
        set_default_decision_client(None)
