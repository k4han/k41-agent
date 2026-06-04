from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import pytest

import agent.modules.workflows.nodes.llm as llm_node_module
from agent.modules.workflows.run_config import WorkflowContext


class _FakeChatModel:
    def __init__(self, captured: dict):
        self._captured = captured

    def bind_tools(self, tools):
        self._captured["tools"] = tools
        return self

    async def ainvoke(self, messages, config=None):
        self._captured["messages"] = messages
        self._captured["config"] = config
        return AIMessage(content="ok")


def _fake_chat_model_factory(captured: dict):
    def _factory(model: str | None = None, *, provider_name: str | None = None):
        captured["provider"] = provider_name
        captured["model"] = model
        return SimpleNamespace(
            model=_FakeChatModel(captured),
            provider_name=provider_name or "default",
            provider_type="openai_compatible",
            model_name=model or "default-model",
        )

    return _factory


@pytest.mark.asyncio
async def test_llm_node_uses_prompt_builder_output_for_system_message(monkeypatch):
    captured: dict = {}
    builder_calls: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            assert name == "builder-agent"
            return SimpleNamespace(
                provider="provider-x",
                model="model-x",
                system_prompt="Agent prompt: {working_dir}",
                tools=["skill", "read_file"],
            )

    def _fake_builder(**kwargs):
        builder_calls.update(kwargs)
        return "Prompt built elsewhere"

    monkeypatch.setattr(
        llm_node_module,
        "get_resolved_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "build_llm_system_prompt",
        _fake_builder,
    )
    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        names = list(override_tool_names) if override_tool_names else []
        return [SimpleNamespace(name=name) for name in names]

    monkeypatch.setattr(
        llm_node_module.ToolResolver,
        "aresolve_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    skill_catalog_calls: list[dict] = []

    async def _fake_effective_skills_catalog(**kwargs):
        skill_catalog_calls.append(kwargs)
        return "<available_skills/>"

    monkeypatch.setattr(
        "agent.modules.skills.get_effective_skills_catalog_xml",
        _fake_effective_skills_catalog,
    )

    result = await llm_node_module.llm_node(
        {"messages": [HumanMessage(content="help me")]},
        {"configurable": {"thread_id": "thread-1"}},
        SimpleNamespace(
            context=WorkflowContext(
                agent_name="builder-agent",
                working_dir="D:/repo",
                max_context_tokens=50000,
                allowed_tool_names=["skill", "read_file"],
                allowed_skill_names=["repo-docs"],
            )
        ),
    )

    assert result["messages"][0].content == "ok"
    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert system_message.content == "Prompt built elsewhere"
    assert captured["model"] == "model-x"
    assert captured["provider"] == "provider-x"
    assert [tool.name for tool in captured["tools"]] == ["skill", "read_file"]
    assert builder_calls["system_prompt_template"] == "Agent prompt: {working_dir}"
    assert builder_calls["working_dir"] == str(Path("D:/repo").resolve())
    assert builder_calls["agent_name"] == "builder-agent"
    assert [tool.name for tool in builder_calls["tools"]] == ["skill", "read_file"]
    assert builder_calls["prompt_variables"] == {}
    assert builder_calls["skills_catalog_xml"] == "<available_skills/>"
    assert skill_catalog_calls[0]["allowed_names"] == ["repo-docs"]
    assert skill_catalog_calls[0]["thread_id"] == "thread-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backend,sandbox_locator,expected_root",
    [
        ("daytona", "sb-daytona-001", "/workspace/facebook/react"),
        ("modal", "sb-modal-001", "/workspace/facebook/react"),
    ],
)
async def test_llm_node_keeps_friendly_workspace_label_and_exposes_actual_sandbox_path(
    monkeypatch, backend, sandbox_locator, expected_root
):
    """{{workspace}} stays as the friendly label, {{working_dir}} carries the real sandbox cwd."""
    captured: dict = {}
    builder_calls: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            assert name == "sandbox-agent"
            return SimpleNamespace(
                provider="default",
                model="model-x",
                system_prompt=(
                    "Workspace label: {{workspace}}\n"
                    "Sandbox cwd: {{working_dir}}"
                ),
                tools=[],
            )

    def _fake_builder(**kwargs):
        builder_calls.update(kwargs)
        return "Prompt built"

    monkeypatch.setattr(
        llm_node_module,
        "get_resolved_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "build_llm_system_prompt",
        _fake_builder,
    )
    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        return []

    monkeypatch.setattr(
        llm_node_module.ToolResolver,
        "aresolve_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    sandbox_workspace = {
        "backend": backend,
        "locator": sandbox_locator,
        "label": "facebook/react",
        "metadata": {
            "root": expected_root,
            "repository_full_name": "facebook/react",
            "source": "github",
        },
    }

    await llm_node_module.llm_node(
        {"messages": [HumanMessage(content="hi")]},
        {"configurable": {"thread_id": "thread-1"}},
        SimpleNamespace(
            context=WorkflowContext(
                agent_name="sandbox-agent",
                workspace=sandbox_workspace,
                max_context_tokens=50000,
                allowed_tool_names=[],
            )
        ),
    )

    assert builder_calls["workspace"] == "facebook/react"
    assert builder_calls["working_dir"] == expected_root


@pytest.mark.asyncio
async def test_llm_node_prefers_runtime_allowed_tool_names_before_building_prompt(monkeypatch):
    captured: dict = {}
    builder_calls: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            assert name == "override-agent"
            return SimpleNamespace(
                provider="default",
                model="model-y",
                system_prompt="Override prompt",
                tools=["read_file"],
            )

    def _fake_builder(**kwargs):
        builder_calls.update(kwargs)
        return "Prompt with overridden tools"

    monkeypatch.setattr(
        llm_node_module,
        "get_resolved_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "build_llm_system_prompt",
        _fake_builder,
    )
    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        names = list(override_tool_names) if override_tool_names else []
        return [SimpleNamespace(name=name) for name in names]

    monkeypatch.setattr(
        llm_node_module.ToolResolver,
        "aresolve_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    await llm_node_module.llm_node(
        {"messages": [HumanMessage(content="hello")]},
        {"configurable": {"thread_id": "thread-1"}},
        SimpleNamespace(
            context=WorkflowContext(
                agent_name="override-agent",
                working_dir="D:/repo",
                max_context_tokens=50000,
                allowed_tool_names=["call_agent", "skill"],
            )
        ),
    )

    assert [tool.name for tool in captured["tools"]] == ["call_agent", "skill"]
    assert [tool.name for tool in builder_calls["tools"]] == ["call_agent", "skill"]


@pytest.mark.asyncio
async def test_llm_node_normalizes_assistant_string_list_history(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            assert name == "history-agent"
            return SimpleNamespace(
                provider="default",
                model="model-y",
                system_prompt="History prompt",
                tools=[],
            )

    monkeypatch.setattr(
        llm_node_module,
        "get_resolved_chat_model",
        _fake_chat_model_factory(captured),
    )
    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        return []

    monkeypatch.setattr(
        llm_node_module.ToolResolver,
        "aresolve_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    original_message = AIMessage(content=["first ", "second"], id="ai-history")

    await llm_node_module.llm_node(
        {
            "messages": [
                HumanMessage(content="hello"),
                original_message,
            ]
        },
        {"configurable": {"thread_id": "thread-1"}},
        SimpleNamespace(
            context=WorkflowContext(
                agent_name="history-agent",
                working_dir="D:/repo",
                max_context_tokens=50000,
                allowed_tool_names=[],
            )
        ),
    )

    normalized_message = captured["messages"][2]
    assert isinstance(normalized_message, AIMessage)
    assert normalized_message.content == "first second"
    assert normalized_message.id == "ai-history"
    assert original_message.content == ["first ", "second"]


@pytest.mark.asyncio
async def test_llm_node_prefers_runtime_model_over_agent_card_model(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            assert name == "override-agent"
            return SimpleNamespace(
                provider="default",
                model="agent-card-model",
                system_prompt="Override prompt",
                tools=[],
            )

    monkeypatch.setattr(
        llm_node_module,
        "get_resolved_chat_model",
        _fake_chat_model_factory(captured),
    )
    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        return []

    monkeypatch.setattr(
        llm_node_module.ToolResolver,
        "aresolve_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    await llm_node_module.llm_node(
        {"messages": [HumanMessage(content="hello")]},
        {"configurable": {"thread_id": "thread-1"}},
        SimpleNamespace(
            context=WorkflowContext(
                agent_name="override-agent",
                working_dir="D:/repo",
                max_context_tokens=50000,
                allowed_tool_names=[],
                model="runtime-model",
            )
        ),
    )

    assert captured["model"] == "runtime-model"
