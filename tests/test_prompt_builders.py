"""Coverage for the workspace-storage prompt section injected by ``build_llm_system_prompt``."""

from __future__ import annotations

from types import SimpleNamespace

from agent.modules.workflows import prompt_builders


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
        assert prompt_builders.WORKSPACE_STORAGE_PROMPT in prompt, tool_name


def test_workspace_storage_prompt_injects_for_generate_image_tool() -> None:
    prompt = _build([SimpleNamespace(name="generate_image")])

    assert prompt_builders.WORKSPACE_STORAGE_PROMPT in prompt
    assert ".k41-agent/generated-images/" in prompt_builders.WORKSPACE_STORAGE_PROMPT


def test_workspace_storage_prompt_absent_without_relevant_tool() -> None:
    prompt = _build([SimpleNamespace(name="web_search"), SimpleNamespace(name="ask_user")])

    assert prompt_builders.WORKSPACE_STORAGE_PROMPT not in prompt
    # Other tool-driven sections should still kick in for control coverage.
    assert prompt_builders.ASK_USER_PROMPT in prompt


def test_workspace_storage_prompt_absent_when_no_tools_bound() -> None:
    prompt = _build([])

    assert prompt_builders.WORKSPACE_STORAGE_PROMPT not in prompt


def test_workspace_storage_instructions_distinguish_projects_notes_and_output() -> None:
    prompt = _build([SimpleNamespace(name="bash"), SimpleNamespace(name="read")])
    assert "never as the project workspace" in prompt
    assert "Keep shell workdir at the workspace" in prompt
    assert "Do not create assets/ or memory/" in prompt
    assert "next_byte_offset" in prompt and "seven days" in prompt
    assert "read_tool_output" not in prompt


def test_workspace_storage_instructions_provide_conversation_draft_directory() -> None:
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base", working_dir="/workspace", agent_name="default",
        tools=[SimpleNamespace(name="write")], catalog=SimpleNamespace(),
        scratchpad_path=".k41-agent/scratchpad/conversation-key/",
    )
    assert "Your draft directory is .k41-agent/scratchpad/conversation-key/" in prompt
