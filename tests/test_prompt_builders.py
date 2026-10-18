"""Coverage for the workspace-storage prompt section injected by ``build_llm_system_prompt``."""

from __future__ import annotations

from types import SimpleNamespace

from agent.modules.workflows import prompt_builders
from agent.modules.workspaces.prompt_context import build_workspace_context


def _build(tools: list[SimpleNamespace]) -> str:
    return prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="default",
        tools=tools,
        catalog=SimpleNamespace(),
    )


def test_workspace_storage_prompt_injects_for_filesystem_tools() -> None:
    for tool_name in ("list_dir", "read", "write", "edit", "glob", "grep"):
        prompt = _build([SimpleNamespace(name=tool_name)])
        assert build_workspace_context() in prompt, tool_name


def test_workspace_storage_prompt_injects_for_generate_image_tool() -> None:
    prompt = _build([SimpleNamespace(name="generate_image")])

    assert build_workspace_context() in prompt
    assert ".k41-agent/generated-images/" in build_workspace_context()


def test_workspace_storage_prompt_absent_without_relevant_tool() -> None:
    prompt = _build([SimpleNamespace(name="web_search"), SimpleNamespace(name="ask_user")])

    assert build_workspace_context() not in prompt
    assert prompt == "Base prompt"


def test_workspace_storage_prompt_absent_when_no_tools_bound() -> None:
    prompt = _build([])

    assert build_workspace_context() not in prompt


def test_workspace_storage_instructions_distinguish_projects_notes_and_output() -> None:
    prompt = _build([SimpleNamespace(name="bash"), SimpleNamespace(name="read")])
    assert "never as the project workspace" in prompt
    assert "Keep shell workdir at the workspace" in prompt
    assert "Do not create assets/ or memory/ inside .k41-agent/" in prompt
    assert "next_byte_offset" not in prompt and "seven days" in prompt
    assert "read_tool_output" not in prompt


def test_workspace_storage_instructions_provide_shared_project_draft_directory() -> None:
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base", working_dir="/workspace", agent_name="default",
        tools=[SimpleNamespace(name="write")], catalog=SimpleNamespace(),
        scratchpad_path=".k41-agent/scratchpad/",
    )
    assert prompt.count(".k41-agent/scratchpad/") == 1
    assert "All conversations and sub-agents in this workspace share these files" in prompt
    assert "Notes survive turns, restarts, and conversation reset or deletion" in prompt
