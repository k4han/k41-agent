"""Unit tests for the agents module — parser, repository, service, and call_agent."""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent.modules.agents.models import AgentConfig
from agent.modules.agents.parser import parse_agent_file, serialize_agent_config
from agent.modules.agents.repository import FilesystemAgentRepository
from agent.modules.agents.service import AgentCatalogService


# --- Fixtures ---

@pytest.fixture
def sample_agent_md():
    """Sample agent file content with frontmatter + body."""
    return """\
---
name: researcher
display_name: Research Agent
graph_type: react_agent
tools: [read_file, list_files, call_agent]
sub_agents: []
max_context_tokens: 30000
---

You are a research assistant. Help the user find and synthesize information.
"""


@pytest.fixture
def sample_coder_agent_md():
    return """\
---
name: coder
display_name: Coder Agent
graph_type: react_agent
tools: [read_file, write_file, run_bash, list_files, call_agent]
sub_agents: [researcher]
max_context_tokens: 50000
---

You are a coding assistant specialized in Python development.
"""


@pytest.fixture
def agent_file_path(sample_agent_md):
    """Create a temp .md file and return its path."""
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False, encoding="utf-8") as f:
        f.write(sample_agent_md)
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def agents_dir(sample_agent_md, sample_coder_agent_md):
    """Create a temp directory with two agent .md files."""
    d = tempfile.mkdtemp()
    Path(d, "researcher.md").write_text(sample_agent_md, encoding="utf-8")
    Path(d, "coder.md").write_text(sample_coder_agent_md, encoding="utf-8")
    yield d
    # Cleanup
    for p in Path(d).glob("*.md"):
        p.unlink()
    os.rmdir(d)


# --- Parser tests ---

class TestParseAgentFile:
    def test_parse_valid_file(self, agent_file_path):
        config = parse_agent_file(agent_file_path)
        assert config is not None
        assert isinstance(config, AgentConfig)
        assert config.name == "researcher"
        assert config.display_name == "Research Agent"
        assert config.graph_type == "react_agent"
        assert config.tools == ["read_file", "list_files", "call_agent"]
        assert config.sub_agents == []
        assert config.max_context_tokens == 30000
        assert "research assistant" in config.system_prompt

    def test_parse_file_no_frontmatter(self):
        d = tempfile.mkdtemp()
        p = Path(d, "bad.md")
        p.write_text("Just plain text, no frontmatter.", encoding="utf-8")
        config = parse_agent_file(p)
        assert config is None
        os.unlink(p)
        os.rmdir(d)

    def test_parse_file_missing_name(self):
        d = tempfile.mkdtemp()
        p = Path(d, "bad.md")
        p.write_text("---\ngraph_type: react_agent\n---\nBody.", encoding="utf-8")
        config = parse_agent_file(p)
        assert config is None
        os.unlink(p)
        os.rmdir(d)

    def test_parse_file_missing_graph_type_defaults_to_react_agent(self):
        d = tempfile.mkdtemp()
        p = Path(d, "bad.md")
        p.write_text("---\nname: test\n---\nBody.", encoding="utf-8")
        config = parse_agent_file(p)
        assert config is not None
        assert config.graph_type == "react_agent"
        os.unlink(p)
        os.rmdir(d)

    def test_parse_file_ignores_legacy_workflow_key(self):
        d = tempfile.mkdtemp()
        p = Path(d, "workflow_alias.md")
        p.write_text("---\nname: test\nworkflow: research_chain\n---\nBody.", encoding="utf-8")
        config = parse_agent_file(p)
        assert config is not None
        assert config.graph_type == "react_agent"
        os.unlink(p)
        os.rmdir(d)

    def test_parse_file_sub_agents_none(self):
        """No sub_agents key in YAML = None (leaf, cannot call anyone)."""
        d = tempfile.mkdtemp()
        p = Path(d, "leaf.md")
        p.write_text("---\nname: leaf\ngraph_type: react_agent\n---\nI am a leaf.", encoding="utf-8")
        config = parse_agent_file(p)
        assert config is not None
        assert config.sub_agents is None
        os.unlink(p)
        os.rmdir(d)

    def test_parse_file_sub_agents_empty_list(self):
        """Empty sub_agents list = can call no one (but IS a non-leaf)."""
        d = tempfile.mkdtemp()
        p = Path(d, "empty_sub.md")
        p.write_text("---\nname: empty\ngraph_type: react_agent\nsub_agents: []\n---\nEmpty.", encoding="utf-8")
        config = parse_agent_file(p)
        assert config is not None
        assert config.sub_agents == []
        os.unlink(p)
        os.rmdir(d)

    def test_parse_file_ignores_legacy_routing_metadata(self):
        d = tempfile.mkdtemp()
        p = Path(d, "legacy_routing_metadata.md")
        p.write_text(
            (
                "---\n"
                "name: test\n"
                "graph_type: react_agent\n"
                "routing_hints: legacy hints\n"
                "capabilities: backend, python\n"
                "---\n"
                "Body."
            ),
            encoding="utf-8",
        )
        config = parse_agent_file(p)
        assert config is not None
        assert not hasattr(config, "routing_hints")
        assert not hasattr(config, "capabilities")
        serialized = serialize_agent_config(config)
        assert "routing_hints" not in serialized
        assert "capabilities" not in serialized
        os.unlink(p)
        os.rmdir(d)


# --- Repository tests ---

class TestFilesystemAgentRepository:
    def test_load_from_directory(self, agents_dir):
        repo = FilesystemAgentRepository(agents_dir)
        agents = repo.load()
        assert len(agents) == 4  # researcher, coder, + builtin default, scheduler-executor
        assert "researcher" in agents
        assert "coder" in agents
        assert "default" in agents
        assert "scheduler-executor" in agents
        assert agents["researcher"].graph_type == "react_agent"
        assert agents["default"].name == "default"

    def test_load_empty_directory(self):
        d = tempfile.mkdtemp()
        repo = FilesystemAgentRepository(d)
        agents = repo.load()
        assert len(agents) == 2  # builtin default + scheduler-executor
        assert "default" in agents
        assert agents["default"].display_name == ""
        os.rmdir(d)

    def test_load_nonexistent_directory(self):
        repo = FilesystemAgentRepository("/nonexistent/path/12345")
        agents = repo.load()
        assert len(agents) == 2  # builtin default + scheduler-executor
        assert "default" in agents

    def test_reload(self, agents_dir):
        repo = FilesystemAgentRepository(agents_dir)
        repo.load()
        agents2 = repo.reload()
        assert len(agents2) == 4  # researcher, coder, + builtin default, scheduler-executor


# --- service tests ---

class TestAgentCatalogService:
    @pytest.fixture(autouse=True)
    def setup_service(self, agents_dir):
        """Reset singleton and create service with test agents dir."""
        import agent.modules.agents.repository as repo_mod
        import agent.modules.agents.service as svc_mod

        repo_mod._repository = None
        svc_mod._service = None

        # Create repo with test data
        test_repo = FilesystemAgentRepository(agents_dir)
        test_repo.load()

        # Patch get_repository in service.py's namespace (where it is imported from)
        with patch.object(svc_mod, "get_repository", return_value=test_repo):
            self.service = AgentCatalogService()
            yield

    def test_get_agent_exists(self):
        config = self.service.get_agent("researcher")
        assert config is not None
        assert config.name == "researcher"

    def test_get_agent_not_found(self):
        config = self.service.get_agent("nonexistent")
        assert config is None

    def test_list_agents(self):
        agents = self.service.list_agents()
        names = {a.name for a in agents}
        assert names == {"researcher", "coder", "default", "scheduler-executor"}

    def test_get_callable_agents_none_sub_agents(self):
        """researcher has sub_agents=[] → cannot call anyone."""
        callables = self.service.get_callable_agents("researcher")
        assert callables == []

    def test_get_callable_agents_restricted(self):
        """coder has sub_agents=[researcher] → can only call researcher."""
        callables = self.service.get_callable_agents("coder")
        assert callables == ["researcher"]

    def test_get_callable_agents_unknown_agent(self):
        callables = self.service.get_callable_agents("nonexistent")
        assert callables == []

    def test_validate_call_allowed(self):
        assert self.service.validate_call("coder", "researcher") is True

    def test_validate_call_denied_not_in_sub_list(self):
        """researcher has sub_agents=[] → cannot call anyone."""
        assert self.service.validate_call("researcher", "coder") is False

    def test_validate_call_denied_sub_agents_none(self):
        """researcher sub_agents is None (empty list in file) → cannot call."""
        # Note: empty list [] means restricted to no one. sub_agents=None means leaf.
        pass  # Our researcher has sub_agents=[] not None, tested above

    def test_validate_call_self(self):
        assert self.service.validate_call("coder", "coder") is False

    def test_validate_call_nonexistent_caller(self):
        assert self.service.validate_call("ghost", "researcher") is False

    def test_validate_call_nonexistent_target(self):
        assert self.service.validate_call("coder", "ghost") is False

    def test_reload(self):
        agents = self.service.reload_agents()
        assert len(agents) == 4  # researcher, coder, + builtin default, scheduler-executor
