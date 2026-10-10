"""Tests for decision-guided context curation workflow graph."""

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
            if schema == FileInclusionDecision:
                return self.inclusion_decision
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
async def test_decision_node_enriches_user_message(tmp_path: Path, monkeypatch):
    """Verify decision_node inspects directory tree, selects files, and injects context."""
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
    enriched_msg = result["messages"][0]
    assert enriched_msg.id == "msg-user-1"
    assert "How is the agent defined?" in enriched_msg.content
    assert CONTEXT_MARKER in enriched_msg.content
    assert "<directory_tree>" in enriched_msg.content
    assert "agent.py" in enriched_msg.content
    assert "class Agent: pass" in enriched_msg.content

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

    class _FakeMainLLM:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages, **kwargs):
            nonlocal call_count
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

    build_decision_agent_graph(graph_name="test_decision_graph")
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

    # Verify the initial HumanMessage was enriched with workspace tree & math_utils.py
    first_human = next(m for m in messages if isinstance(m, HumanMessage))
    assert "Calculate 20 + 22" in first_human.content
    assert "<workspace_context>" in first_human.content
    assert "math_utils.py" in first_human.content
    assert "def add(a, b)" in first_human.content


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
        assert "class MyService" in result["messages"][0].content
        assert "Cloudflare Clef decision" in result["included_files"][0]["reasoning"]
    finally:
        set_default_decision_client(None)
