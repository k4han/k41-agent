import agent.modules.workflows.infrastructure.langgraph.tools.skills as skills_tool_module


def test_skill_tool_returns_structured_content(monkeypatch):
    monkeypatch.setattr(
        skills_tool_module,
        "get_skill_content_xml",
        lambda name: f'<skill_content name="{name}">Body</skill_content>',
    )

    result = skills_tool_module.skill.invoke({"name": "data-analysis"})

    assert '<skill_content name="data-analysis">' in result


def test_skill_tool_returns_error_when_not_found(monkeypatch):
    monkeypatch.setattr(
        skills_tool_module,
        "get_skill_content_xml",
        lambda name: None,
    )

    result = skills_tool_module.skill.invoke({"name": "missing-skill"})

    assert result == "[error] skill not found"
