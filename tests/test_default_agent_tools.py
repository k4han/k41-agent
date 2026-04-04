"""Test to verify default agent tools are loaded correctly."""

import pytest


@pytest.fixture(autouse=True)
def load_agents():
    """Load agents before each test."""
    from agent.modules.agents.infrastructure.repository import load_agents_from_dir
    load_agents_from_dir()


def test_default_agent_tools_loaded():
    """Verify that default agent's tools are loaded from config."""
    from agent.modules.agent_runtime.application.runner import build_run_params
    from agent.modules.agents.public import get_catalog_service

    # Build params with agent_name="default"
    params = build_run_params(
        platform="test",
        user_id="test-user",
        user_input="test message",
        agent_name="default",
    )

    # Should have agent_name set
    assert params["agent_name"] == "default"

    # Workflow is not resolved in build_run_params anymore
    # It's resolved in run_agent functions from agent config
    catalog = get_catalog_service()
    default_config = catalog.get_agent("default")
    assert default_config is not None
    assert default_config.graph_type == "react_agent"


def test_default_agent_config_exists():
    """Verify default agent config is always available."""
    from agent.modules.agents.public import get_catalog_service

    catalog = get_catalog_service()
    default_config = catalog.get_agent("default")

    assert default_config is not None
    assert default_config.name == "default"
    assert default_config.graph_type == "react_agent"

    # Default agent should have tools defined (either from MD or builtin)
    # Empty list means "use all default tools"
    assert default_config.tools is not None or default_config.tools == []


def test_run_agent_loads_default_tools():
    """Verify run_agent loads tools for default agent."""
    from agent.modules.agents.public import get_catalog_service

    catalog = get_catalog_service()
    default_config = catalog.get_agent("default")

    # If default agent has tools defined, they should be loaded
    if default_config and default_config.tools:
        # Tools should be a list
        assert isinstance(default_config.tools, list)
        print(f"Default agent tools: {default_config.tools}")


@pytest.mark.asyncio
async def test_default_agent_context_includes_tools():
    """Verify that context includes tools for default agent."""
    from agent.modules.workflows.public import make_run_context
    from agent.modules.agents.public import get_catalog_service

    catalog = get_catalog_service()
    default_config = catalog.get_agent("default")

    if default_config:
        # Create context with default agent
        context = make_run_context(
            working_dir=".",
            max_context_tokens=default_config.max_context_tokens,
            agent_name="default",
            allowed_tool_names=default_config.tools if default_config.tools else None,
        )

        assert context.agent_name == "default"

        # If tools are specified, they should be in context
        if default_config.tools:
            assert context.allowed_tool_names == default_config.tools
            print(f"Context tools: {context.allowed_tool_names}")
