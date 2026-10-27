"""System prompt variables describe tool execution rather than the application host."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.modules.prompt_variables.environment import get_execution_environment
from agent.modules.prompt_variables.service import get_runtime_prompt_variable_values
from agent.modules.tools.coding.remote_worker import SandboxWorker
from agent.modules.workflows.prompt_builders import build_llm_system_prompt
from agent.modules.workspaces import WorkspaceRef


SANDBOX_ENVIRONMENT = {
    "operating_system": "linux",
    "host_os": "linux",
    "shell_name": "bash",
    "shell_kind": "posix",
    "user_name": "sandbox-user",
}


@pytest.fixture(params=["daytona", "modal"])
def sandbox_environment(request, monkeypatch):
    workspace = WorkspaceRef(
        backend=request.param, locator="sandbox-123", label="org/repo",
        metadata={"root": "workspace/repo"},
    )
    client = SimpleNamespace(
        backend=SimpleNamespace(root="/home/sandbox/workspace/repo"),
        ensure_started=AsyncMock(), call=AsyncMock(return_value=dict(SANDBOX_ENVIRONMENT)),
    )
    remote = SimpleNamespace(client=AsyncMock(return_value=client))
    service = SimpleNamespace(shell="powershell.exe", remote=remote)
    monkeypatch.setattr("agent.modules.tools.coding.service.get_coding_service", lambda: service)
    monkeypatch.setattr(
        "agent.modules.prompt_variables.service.get_prompt_variable_values",
        AsyncMock(return_value={"common_rules": "Follow project conventions."}),
    )
    return workspace, client, service


@pytest.mark.asyncio
async def test_sandbox_variables_use_remote_environment_and_resolved_root(sandbox_environment):
    workspace, client, service = sandbox_environment
    variables = await get_runtime_prompt_variable_values(workspace, thread_id="thread-123")
    assert variables == {
        **SANDBOX_ENVIRONMENT, "common_rules": "Follow project conventions.",
        "workspace": "org/repo", "working_dir": client.backend.root,
    }
    context = service.remote.client.call_args.args[0]
    assert (context.backend, context.locator, context.workspace, context.thread_id) == (
        workspace.backend, workspace.locator, "workspace/repo", "thread-123",
    )
    client.ensure_started.assert_awaited_once()
    client.call.assert_awaited_once_with({"operation": "environment"})
    prompt = build_llm_system_prompt(
        system_prompt_template="|".join("{{" + name + "}}" for name in variables),
        working_dir=variables["working_dir"], workspace=variables["workspace"],
        agent_name="default", tools=[], catalog=None, prompt_variables=variables,
    )
    assert prompt == "|".join(variables.values())
    assert "powershell" not in prompt


@pytest.mark.asyncio
async def test_sandbox_environment_errors_do_not_fall_back_to_host(sandbox_environment):
    workspace, client, _ = sandbox_environment
    client.call.side_effect = RuntimeError("Sandbox unavailable")
    with pytest.raises(RuntimeError, match="Sandbox unavailable"):
        await get_runtime_prompt_variable_values(workspace)


@pytest.mark.asyncio
async def test_internal_card_uses_execution_workspace(sandbox_environment, monkeypatch):
    from agent.modules.agents import render_agent_prompt

    workspace, client, _ = sandbox_environment
    monkeypatch.setattr(
        "agent.modules.agents.prompts.get_agent_prompt_template",
        lambda name: "{{common_rules}} {{host_os}} {{shell_name}} {{user_name}} {working_dir} {{max_candidates}}",
    )
    prompt = await render_agent_prompt(
        "context-file-selector", workspace=workspace, values={"max_candidates": 3},
    )
    assert prompt == f"Follow project conventions. linux bash sandbox-user {client.backend.root} 3"


@pytest.mark.asyncio
async def test_research_cards_share_sandbox_variables(sandbox_environment, monkeypatch):
    from agent.modules.workflows.graphs.research import _research_system

    workspace, client, _ = sandbox_environment
    monkeypatch.setattr(
        "agent.modules.agents.prompts.get_agent_prompt_template",
        lambda name: "Research on {{host_os}} with {{shell_name}} as {{user_name}}.",
    )
    context = SimpleNamespace(
        get_workspace=lambda: workspace, get_working_dir=lambda: "workspace/repo",
        get_agent_name=lambda: "researcher",
    )
    message = await _research_system(
        "research-planner", SimpleNamespace(context=context),
        SimpleNamespace(system_prompt="Work on {{host_os}} at {working_dir}."),
    )
    assert message.content == (
        "Research on linux with bash as sandbox-user.\n\n<agent_instructions>\n"
        f"Work on linux at {client.backend.root}.\n</agent_instructions>"
    )
    client.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_variables_resolve_without_persistence(sandbox_environment, monkeypatch):
    workspace, _, _ = sandbox_environment
    monkeypatch.setattr(
        "agent.modules.prompt_variables.service.get_prompt_variable_values",
        AsyncMock(side_effect=RuntimeError("Async engine not initialized")),
    )
    variables = await get_runtime_prompt_variable_values(workspace)
    assert variables["host_os"] == "linux"
    assert "common_rules" not in variables


@pytest.mark.asyncio
async def test_local_variables_use_configured_coding_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent.modules.tools.coding.service.get_coding_service",
        lambda: SimpleNamespace(shell="custom-shell"),
    )
    monkeypatch.setattr(
        "agent.modules.prompt_variables.service.get_prompt_variable_values",
        AsyncMock(return_value={}),
    )
    workspace = WorkspaceRef(locator=str(tmp_path), label="Local project")
    variables = await get_runtime_prompt_variable_values(workspace)
    assert variables == {
        **get_execution_environment("custom-shell"),
        "workspace": "Local project", "working_dir": str(tmp_path),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("package_export", [True, False])
async def test_worker_reports_its_own_environment(monkeypatch, package_export):
    if not package_export:
        monkeypatch.delattr("agent.modules.prompt_variables.get_execution_environment")
    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.resolve_shell", lambda: "/bin/sh")
    worker = object.__new__(SandboxWorker)
    response = await worker.dispatch({"operation": "environment"})
    assert response == get_execution_environment("/bin/sh")
    assert response["shell_name"] == "sh"
    assert response["shell_kind"] == "posix"
