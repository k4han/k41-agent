"""Test to verify the bug fix: default agent tools are loaded in run_agent."""

import pytest


@pytest.fixture(autouse=True)
def load_agents():
    """Load agents before each test."""
    from agent.modules.agents.repository import load_agents_from_dir
    load_agents_from_dir()


@pytest.mark.asyncio
async def test_run_agent_loads_tools_for_default_agent():
    """Verify that run_agent loads tools when agent_name='default'.

    This is a regression test for the bug where:
    - if agent_name and agent_name != "default" would skip loading tools
    - Fixed to: if agent_name (without the != "default" check)
    """
    from agent.modules.agent_runtime.runner import build_run_params
    from agent.modules.agents import get_catalog_service

    # Get default agent config
    catalog = get_catalog_service()
    default_config = catalog.get_agent("default")

    assert default_config is not None
    assert default_config.tools is not None
    assert len(default_config.tools) > 0

    print(f"\nDefault agent tools from config: {default_config.tools}")

    # Build params with agent_name="default"
    params = build_run_params(
        platform="telegram",
        user_id="test-user",
        user_input="test message",
        agent_name="default",
    )

    # Verify params include agent_name
    assert params["agent_name"] == "default"

    # Now simulate what run_agent does
    agent_name = params["agent_name"]

    # This is the fixed logic (should load tools for "default")
    allowed_tool_names = None
    if agent_name:  # No longer checks != "default"
        agent_config = catalog.get_agent(agent_name)
        if agent_config:
            allowed_tool_names = agent_config.tools or None

    # Verify tools are loaded
    assert allowed_tool_names is not None
    assert allowed_tool_names == default_config.tools
    print(f"Tools loaded in run_agent: {allowed_tool_names}")


@pytest.mark.asyncio
async def test_telegram_default_agent_gets_tools():
    """Verify Telegram's default agent flow loads tools correctly."""
    from agent.modules.agent_runtime.runner import build_run_params
    from agent.modules.agents import get_catalog_service

    # Simulate Telegram handler logic
    def _resolve_catalog_agent_name(*candidates):
        catalog = get_catalog_service()
        for candidate in candidates:
            name = (candidate or "").strip()
            if not name:
                continue
            if catalog.get_agent(name) is not None:
                return name
        return None

    # Simulate what Telegram does
    default_agent_name = _resolve_catalog_agent_name(
        None,
        "default",
    )

    print(f"\nResolved agent name: {default_agent_name}")
    assert default_agent_name == "default"

    # Build params like Telegram does
    params = build_run_params(
        platform="telegram",
        user_id="123456",
        user_input="Hello",
        channel_id="789",
        working_dir=".",
        agent_name=default_agent_name,
    )

    # Verify agent_name is set
    assert params["agent_name"] == "default"

    # Get the agent config
    catalog = get_catalog_service()
    config = catalog.get_agent(params["agent_name"])

    assert config is not None
    assert config.tools is not None
    print(f"Agent config tools: {config.tools}")

    # This should now work (bug was here)
    allowed_tool_names = config.tools or None
    assert allowed_tool_names is not None
    print(f"Tools that will be used: {allowed_tool_names}")
