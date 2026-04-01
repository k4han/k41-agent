"""Integration test for agent-based workflow execution."""

import pytest
from langchain_core.messages import HumanMessage

from agent.modules.agents.infrastructure.repository import FilesystemAgentRepository
from agent.modules.agents.application.service import AgentCatalogService
from agent.modules.workflows.public import get_workflow_graph, make_run_config, make_run_context


@pytest.fixture
def test_agent_dir(tmp_path):
    """Create test agents directory with sample agents."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Create a simple test agent
    test_agent = agents_dir / "test-agent.md"
    test_agent.write_text("""---
name: "test-agent"
description: "Test agent for integration testing"
graph_type: "react_agent"
model: "devstral-2512"
tools:
  - "list_files"
max_context_tokens: 10000
---

# System Prompt

You are a test assistant. Always respond with "Test response from test-agent".
""")

    return str(agents_dir)


@pytest.mark.asyncio
async def test_agent_workflow_integration(test_agent_dir):
    """Test complete flow: agent_name → config → workflow → llm_node."""

    # 1. Load agent from directory
    repo = FilesystemAgentRepository(test_agent_dir)
    agents = repo.load()

    assert "test-agent" in agents
    assert "default" in agents  # Builtin default

    # 2. Get agent config via service
    service = AgentCatalogService()
    service._repository = repo  # Inject test repo

    config = service.get_agent("test-agent")
    assert config is not None
    assert config.name == "test-agent"
    assert config.graph_type == "react_agent"
    assert config.model == "devstral-2512"
    assert "list_files" in config.tools

    # 3. Build context with agent_name
    context = make_run_context(
        service_type=config.service_type,
        working_dir=".",
        max_context_tokens=config.max_context_tokens,
        agent_name=config.name,
        allowed_tool_names=config.tools,
    )

    assert context["agent_name"] == "test-agent"
    assert context["max_context_tokens"] == 10000
    assert context["allowed_tool_names"] == ["list_files"]

    # Verify config resolution chain is complete
    # (We don't invoke the graph to avoid API calls)


def test_default_agent_always_available():
    """Test that default agent is always available even without MD files."""
    import tempfile
    import os

    empty_dir = tempfile.mkdtemp()
    try:
        repo = FilesystemAgentRepository(empty_dir)
        agents = repo.load()

        assert len(agents) == 1
        assert "default" in agents

        default = agents["default"]
        assert default.name == "default"
        assert default.display_name == "Default Assistant"
        assert default.graph_type == "react_agent"
        assert default.model == "devstral-2512"
        assert default.system_prompt == "You are a helpful AI assistant.\nWorking directory: {working_dir}"
    finally:
        os.rmdir(empty_dir)


def test_agent_config_overrides_defaults(test_agent_dir):
    """Test that agent config properly overrides default values."""
    repo = FilesystemAgentRepository(test_agent_dir)
    agents = repo.load()

    test_agent = agents["test-agent"]
    default_agent = agents["default"]

    # Test agent has custom config
    assert test_agent.model == "devstral-2512"
    assert test_agent.tools == ["list_files"]
    assert "Test response" in test_agent.system_prompt

    # Default agent has builtin config
    assert default_agent.model == "devstral-2512"
    assert default_agent.tools == []  # Empty = all default tools
    assert "helpful AI assistant" in default_agent.system_prompt


def test_agent_with_sub_agents(tmp_path):
    """Test agent hierarchy with sub_agents."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Parent agent
    parent = agents_dir / "parent.md"
    parent.write_text("""---
name: "parent"
graph_type: "react_agent"
tools:
  - "call_agent"
sub_agents:
  - "child"
---

Parent agent that can call child.
""")

    # Child agent (leaf)
    child = agents_dir / "child.md"
    child.write_text("""---
name: "child"
graph_type: "react_agent"
tools:
  - "list_files"
---

Child agent (leaf node).
""")

    repo = FilesystemAgentRepository(str(agents_dir))
    agents = repo.load()

    service = AgentCatalogService()
    service._repository = repo

    # Verify hierarchy
    assert service.validate_call("parent", "child") is True
    assert service.validate_call("child", "parent") is False  # child is leaf
    assert service.validate_call("parent", "parent") is False  # no self-calls

    callable = service.get_callable_agents("parent")
    assert callable == ["child"]

    callable_child = service.get_callable_agents("child")
    assert callable_child == []  # leaf node
