from types import SimpleNamespace

import pytest

import agent.modules.tools.langchain.skill_tools.skill as skills_tool_module


@pytest.mark.asyncio
async def test_skill_tool_returns_structured_content(monkeypatch):
    async def fake_get_effective_skill_content_xml(name, **kwargs):
        assert kwargs["allowed_names"] is None
        return f'<skill_content name="{name}">Body</skill_content>'

    monkeypatch.setattr(
        skills_tool_module,
        "get_effective_skill_content_xml",
        fake_get_effective_skill_content_xml,
    )

    runtime = SimpleNamespace(context={}, config={"configurable": {"thread_id": "t1"}})
    result = await skills_tool_module.skill.coroutine(
        name="data-analysis",
        runtime=runtime,
    )

    assert '<skill_content name="data-analysis">' in result


@pytest.mark.asyncio
async def test_skill_tool_returns_error_when_not_found(monkeypatch):
    async def fake_get_effective_skill_content_xml(name, **kwargs):
        return None

    monkeypatch.setattr(
        skills_tool_module,
        "get_effective_skill_content_xml",
        fake_get_effective_skill_content_xml,
    )

    runtime = SimpleNamespace(context={}, config={})
    result = await skills_tool_module.skill.coroutine(
        name="missing-skill",
        runtime=runtime,
    )

    assert result == "[error] not_found: skill not found"


@pytest.mark.asyncio
async def test_skill_tool_returns_permission_error_when_not_allowed(monkeypatch):
    async def fake_get_effective_skill_content_xml(name, **kwargs):
        assert kwargs["allowed_names"] == ["allowed-skill"]
        return None

    monkeypatch.setattr(
        skills_tool_module,
        "get_effective_skill_content_xml",
        fake_get_effective_skill_content_xml,
    )

    runtime = SimpleNamespace(context={"allowed_skill_names": ["allowed-skill"]}, config={})
    result = await skills_tool_module.skill.coroutine(
        name="missing-skill",
        runtime=runtime,
    )

    assert result == "[error] permission_denied: skill is not available"
