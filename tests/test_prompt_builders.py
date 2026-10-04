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
