"""Prompt overrides and backend policies reach the actual model/task input."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.modules.agents import AgentCatalogService, AgentConfig, FilesystemAgentRepository, render_agent_prompt
from agent.modules.github.prompts import build_agent_prompt, build_manual_task_prompt
from agent.modules.github.service import GitHubTaskContext
from agent.modules.workflows.graphs.research import _research_system
from agent.modules.workflows.nodes.decision import FileCandidateSelection, _decide_candidate_files_llm
from agent.modules.workflows.run_config import WorkflowContext


@pytest.fixture
def prompt_catalog(tmp_path, monkeypatch):
    service = AgentCatalogService(FilesystemAgentRepository(tmp_path / "cards"))
    service.reload_agents()
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: service)
    return service


def _override(service, name, prompt):
    card = service.clone_builtin_agent(name)
    service.update_agent_card(card.name, card.to_agent_config().model_copy(update={"system_prompt": prompt}))


@pytest.mark.asyncio
async def test_internal_prompt_overrides_resolve_runtime_values_without_overwriting_unknown_variables(prompt_catalog):
    _override(prompt_catalog, "context-file-selector", "{{rules}} Select {{max_candidates}} at {working_dir}. {{unknown}}")
    result = await render_agent_prompt(
        "context-file-selector", values={"max_candidates": 3}, working_dir="/project",
        prompt_variables={"rules": "Prefer source files.", "max_candidates": "999"},
    )
    assert result == "Prefer source files. Select 3 at /project. {{unknown}}"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["research-planner", "research-summarizer"])
async def test_research_uses_step_card_and_executing_agent_instructions(prompt_catalog, tmp_path, name):
    _override(prompt_catalog, name, f"Custom {name} instructions.")
    context = WorkflowContext(working_dir=str(tmp_path))
    caller = AgentConfig(name="caller", graph_type="research_chain", system_prompt="Follow project rules at {{working_dir}}.")
    message = await _research_system(name, SimpleNamespace(context=context), caller)
    assert f"Custom {name} instructions." in message.content
    assert f"Follow project rules at {tmp_path}." in message.content


@pytest.mark.asyncio
async def test_file_selection_uses_overridden_card_and_keeps_structured_schema(prompt_catalog):
    _override(prompt_catalog, "context-file-selector", "Inspect at most {{max_candidates}} source files.")
    captured = {}

    class Model:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return self

        async def ainvoke(self, messages, **kwargs):
            captured["messages"] = messages
            return FileCandidateSelection(candidate_files=["app.py"])

    result = await _decide_candidate_files_llm(
        Model(), "Find the app", "app.py", caller_agent_name="caller",
        provider_name="test", model_name="test", model_kwargs={}, max_candidates=2,
    )
    assert result.candidate_files == ["app.py"]
    assert captured["schema"] is FileCandidateSelection
    assert captured["messages"][0].content == "Inspect at most 2 source files."
    assert "Find the app" in captured["messages"][1].content


@pytest.mark.asyncio
async def test_empty_internal_card_is_rejected(prompt_catalog):
    _override(prompt_catalog, "research-planner", "")
    with pytest.raises(RuntimeError, match="non-empty system prompt"):
        await render_agent_prompt("research-planner", prompt_variables={})


@pytest.mark.parametrize("event", ["issues", "pull_request_review_comment"])
def test_github_automation_uses_shared_policy_and_preserves_task_data(prompt_catalog, tmp_path, event):
    _override(prompt_catalog, "github-automation-policy", "Backend manages commits. Inspect before editing.")
    context = GitHubTaskContext(
        installation_id=1, repository_full_name="org/repo", issue_number=12,
        issue_title="Fix a bug", issue_url="https://example.test/12", branch="fix-12",
        base_branch="main", workspace_path=Path(tmp_path), repository_instructions="Run project checks.",
    )
    payload = {"issue": {"body": "Failure details"}, "comment": {"body": "Fix this", "path": "app.py", "line": 7}}
    prompt = build_agent_prompt(
        event=event, payload=payload, context=context,
        issue_context={"linked_pull_requests": [{"number": 13, "title": "Existing fix"}]},
    )
    assert prompt.count("Backend manages commits.") == 1
    assert "Run project checks." in prompt
    assert "org/repo" in prompt and "fix-12" in prompt
    if event == "issues":
        assert "Failure details" in prompt and "PR #13" in prompt
    else:
        assert "app.py" in prompt and "Line: 7" in prompt
    manual = build_manual_task_prompt(request="Inspect", repository_full_name="org/repo", repository_instructions="Run checks.")
    assert "Backend manages commits." not in manual
    assert "Run checks." in manual
