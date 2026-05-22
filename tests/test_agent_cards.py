from pathlib import Path

import pytest

from agent.modules.agents.models import AgentConfig
from agent.modules.agents.parser import parse_agent_file
from agent.modules.agents.repository import FilesystemAgentRepository
from agent.modules.agents.service import AgentCatalogService


def _make_service(agents_dir: Path) -> tuple[AgentCatalogService, FilesystemAgentRepository]:
    repo = FilesystemAgentRepository(agents_dir)
    repo.load()
    service = AgentCatalogService()
    service._repository = repo
    return service, repo


def _config(name: str, *, sub_agents: list[str] | None = None) -> AgentConfig:
    return AgentConfig(
        name=name,
        display_name="Sample",
        description="Sample agent",
        graph_type="react_agent",
        provider="default",
        model="",
        tools=["read_file"],
        sub_agents=sub_agents,
        max_context_tokens=1000,
        system_prompt="You are a sample agent.",
    )


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


@pytest.mark.parametrize(
    "config, expected",
    [
        (_config("../bad"), "Agent name can only contain"),
        (
            AgentConfig(
                name="bad-tokens",
                graph_type="react_agent",
                provider="default",
                max_context_tokens=0,
                system_prompt="Prompt",
            ),
            "max_context_tokens",
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
