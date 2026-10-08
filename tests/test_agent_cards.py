from pathlib import Path

import pytest

from agent.modules.agents.models import AgentCard, AgentConfig
from agent.modules.agents.parser import AgentMarkdownError, parse_agent_file, parse_agent_markdown_content, serialize_agent_config
from agent.modules.agents.repository import FilesystemAgentRepository
from agent.modules.agents.service import AgentCatalogService


def _make_service(agents_dir: Path) -> tuple[AgentCatalogService, FilesystemAgentRepository]:
    repo = FilesystemAgentRepository(agents_dir)
    repo.load()
    service = AgentCatalogService()
    service._repository = repo
    return service, repo


def _config(
    name: str,
    *,
    sub_agents: list[str] | None = None,
    plan_approval_targets: list[str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        display_name="Sample",
        description="Sample agent",
        graph_type="react_agent",
        provider="default",
        model="",
        tools=["read"],
        sub_agents=sub_agents,
        plan_approval_targets=plan_approval_targets or [],
        max_context_tokens=1000,
        system_prompt="You are a sample agent.",
    )


@pytest.mark.parametrize("model", [
    "claude-opus-5-5",
    {"id": "claude-opus-5-5"},
    {"id": "claude-opus-5-5", "effort": "low"},
])
def test_agent_model_settings_round_trip(model) -> None:
    import yaml

    content = "---\n" + yaml.safe_dump({"name": "sample", "provider": "default", "model": model}) + "---\nPrompt\n"
    config = parse_agent_markdown_content(content)
    assert config.model == "claude-opus-5-5"
    assert config.reasoning_effort == (model.get("effort") if isinstance(model, dict) else None)
    card = AgentCard.from_config(config, source="user", path="sample.md", editable=True)
    assert card.to_agent_config() == config
    serialized = serialize_agent_config(card.to_agent_config())
    assert parse_agent_markdown_content(serialized) == config
    saved_model = yaml.safe_load(serialized.split("---")[1])["model"]
    assert saved_model == (model if config.reasoning_effort else "claude-opus-5-5")


@pytest.mark.parametrize("model", [
    {"effort": "low"},
    {"id": 123, "effort": "low"},
    {"id": "claude-opus-5-5", "effort": "High"},
    {"id": "claude-opus-5-5", "effort": ""},
    {"id": "claude-opus-5-5", "effort": 123},
    {"id": "claude-opus-5-5", "efort": "low"},
])
def test_agent_model_settings_reject_invalid_values(model) -> None:
    import yaml

    content = "---\n" + yaml.safe_dump({"name": "sample", "provider": "default", "model": model}) + "---\nPrompt\n"
    with pytest.raises(AgentMarkdownError):
        parse_agent_markdown_content(content)


def test_agent_card_crud_preserves_model_effort(tmp_path: Path) -> None:
    service, _ = _make_service(tmp_path / "agents")
    config = _config("sample")
    config.model = "claude-opus-5-5"
    config.reasoning_effort = "low"
    created = service.create_agent_card(config)
    assert created.reasoning_effort == "low"
    service.reload_agents()
    assert service.get_agent("sample").reasoning_effort == "low"
    updated = created.to_agent_config()
    updated.reasoning_effort = "high"
    card = service.update_agent_card("sample", updated)
    assert parse_agent_file(card.path).reasoning_effort == "high"
    updated.reasoning_effort = None
    card = service.update_agent_card("sample", updated)
    assert parse_agent_file(card.path).reasoning_effort is None


@pytest.mark.parametrize("nested, flat", [("low", "high"), ("low", None), (None, "low")])
def test_agent_model_rejects_conflicting_effort(nested, flat):
    import yaml
    from pydantic import ValidationError
    from agent.delivery.http.dashboard.routes.agents import AgentCardBody

    data = {
        "name": "sample", "graph_type": "react_agent", "provider": "default",
        "model": {"id": "claude-opus-5-5", "effort": nested}, "reasoning_effort": flat,
    }
    for cls in (AgentConfig, AgentCardBody):
        with pytest.raises(ValidationError, match="Conflicting"):
            cls.model_validate(data)
    with pytest.raises(ValidationError, match="Conflicting"):
        AgentCard.model_validate({**data, "source": "user", "path": "sample.md"})
    content = "---\n" + yaml.safe_dump(data) + "---\nPrompt\n"
    with pytest.raises(AgentMarkdownError, match="Conflicting"):
        parse_agent_markdown_content(content)


@pytest.mark.parametrize("effort", ["low", None])
def test_agent_model_accepts_matching_effort(effort):
    import yaml
    from agent.delivery.http.dashboard.routes.agents import AgentCardBody

    data = {
        "name": "sample", "graph_type": "react_agent", "provider": "default",
        "model": {"id": "claude-opus-5-5", "effort": effort}, "reasoning_effort": effort,
    }
    assert AgentConfig.model_validate(data).reasoning_effort == effort
    assert AgentCardBody.model_validate(data).reasoning_effort == effort
    content = "---\n" + yaml.safe_dump(data) + "---\nPrompt\n"
    assert parse_agent_markdown_content(content).reasoning_effort == effort


def test_agent_cards_include_source_metadata_and_user_override(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "default.md").write_text(
        """---
name: default
graph_type: react_agent
provider: default
tools: []
max_context_tokens: 1000
---
User default prompt.
""",
        encoding="utf-8",
    )

    service, _ = _make_service(agents_dir)
    cards = {card.name: card for card in service.list_agent_cards() if card.valid}

    assert cards["default"].source == "user"
    assert cards["default"].editable is True
    assert cards["default"].overrides_builtin is True
    assert cards["conversation-title"].source == "builtin"
    assert cards["conversation-title"].editable is False
    assert cards["conversation-title"].hidden is True
    assert cards["scheduler-executor"].source == "builtin"
    assert cards["scheduler-executor"].editable is False
    assert cards["scheduler-executor"].hidden is True
    assert cards["channel-agent"].source == "builtin"
    assert cards["channel-agent"].editable is False
    assert cards["channel-agent"].hidden is False
    assert cards["router"].source == "builtin"
    assert cards["router"].editable is False
    assert cards["router"].hidden is False
    assert cards["router"].graph_type == "router"
    assert cards["router"].sub_agents == ["default", "channel-agent", "github-issue-fixer"]


def test_agent_card_create_update_delete_preserves_sub_agent_semantics(
    tmp_path: Path,
) -> None:
    service, _ = _make_service(tmp_path / "agents")

    created = service.create_agent_card(_config("sample", sub_agents=None))
    created_path = Path(created.path)

    assert created.source == "user"
    assert created_path.name == "sample.md"
    assert parse_agent_file(created_path).sub_agents is None

    updated_config = _config("sample", sub_agents=[])
    updated = service.update_agent_card("sample", updated_config)

    assert updated.sub_agents == []
    assert parse_agent_file(updated.path).sub_agents == []

    service.delete_agent_card("sample")

    assert not created_path.exists()
    assert service.get_agent("sample") is None


def test_agent_card_preserves_plan_approval_targets(tmp_path: Path) -> None:
    service, _ = _make_service(tmp_path / "agents")

    service.create_agent_card(_config("worker"))
    created = service.create_agent_card(
        _config("planner", plan_approval_targets=["worker"])
    )

    assert created.plan_approval_targets == ["worker"]
    parsed = parse_agent_file(Path(created.path))
    assert parsed is not None
    assert parsed.plan_approval_targets == ["worker"]


@pytest.mark.parametrize(
    "targets, expected",
    [
        (["planner"], "planner"),
        (["missing"], "missing"),
    ],
)
def test_agent_card_validation_rejects_invalid_plan_approval_targets(
    tmp_path: Path,
    targets: list[str],
    expected: str,
) -> None:
    service, _ = _make_service(tmp_path / "agents")

    with pytest.raises(ValueError, match=expected):
        service.create_agent_card(
            _config("planner", plan_approval_targets=targets)
        )


def test_clone_builtin_agent_creates_user_override_and_rejects_collision(
    tmp_path: Path,
) -> None:
    service, _ = _make_service(tmp_path / "agents")

    cloned = service.clone_builtin_agent("default")

    assert cloned.source == "user"
    assert cloned.overrides_builtin is True
    assert Path(cloned.path).name == "default.md"

    with pytest.raises(FileExistsError):
        service.clone_builtin_agent("default")


def test_clone_hidden_builtin_agent_preserves_hidden_flag(tmp_path: Path) -> None:
    service, _ = _make_service(tmp_path / "agents")

    cloned = service.clone_builtin_agent("conversation-title")

    assert cloned.source == "user"
    assert cloned.overrides_builtin is True
    assert cloned.hidden is True

    # Verify the hidden flag is persisted in the file
    from agent.modules.agents.parser import parse_agent_file

    parsed = parse_agent_file(Path(cloned.path))
    assert parsed is not None
    assert parsed.hidden is True


def test_clone_builtin_router_agent_preserves_router_contract(tmp_path: Path) -> None:
    service, _ = _make_service(tmp_path / "agents")

    cloned = service.clone_builtin_agent("router")

    assert cloned.source == "user"
    assert cloned.overrides_builtin is True
    assert cloned.graph_type == "router"
    assert Path(cloned.path).name == "router.md"

    from agent.modules.agents.parser import parse_agent_file

    parsed = parse_agent_file(Path(cloned.path))
    assert parsed is not None
    assert parsed.graph_type == "router"
    assert "{agent_options}" in parsed.system_prompt
    assert "{user_input}" in parsed.system_prompt


@pytest.mark.parametrize(
    "config, expected",
    [
        (_config("../bad"), "Agent name can only contain"),
        (
            AgentConfig(
                name="bad-tokens",
                graph_type="react_agent",
                provider="default",
                context_trim_threshold=0,
                system_prompt="Prompt",
            ),
            "context_trim_threshold",
        ),
        (
            AgentConfig(
                name="bad-router",
                graph_type="router",
                provider="default",
                sub_agents=[],
                system_prompt="Choose an agent.",
            ),
            "Router agent system_prompt",
        ),
    ],
)
def test_agent_card_validation_rejects_invalid_save_payloads(
    tmp_path: Path,
    config: AgentConfig,
    expected: str,
) -> None:
    service, _ = _make_service(tmp_path / "agents")

    with pytest.raises(ValueError, match=expected):
        service.create_agent_card(config)
