"""Coverage for the system prompt cache wired into ``llm_node``."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
import pytest

import agent.modules.workflows.nodes.llm as llm_node_module
from agent.modules.workflows.run_config import WorkflowContext
from agent.shared.infrastructure.revisions import (
    PROMPT_VARIABLES_REVISION,
    bump_revision,
)


class _FakeChatModel:
    def __init__(self, captured: dict):
        self._captured = captured

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        self._captured.setdefault("system_prompts", []).append(messages[0].content)
        return AIMessage(content="ok")


def _fake_chat_model_factory(captured: dict):
    def _factory(model: str | None = None, *, provider_name: str | None = None):
        return SimpleNamespace(
            model=_FakeChatModel(captured),
            provider_name=provider_name or "default",
            provider_type="openai_compatible",
            model_name=model or "default-model",
        )

    return _factory


@pytest.fixture
def cached_llm_node(monkeypatch):
    """Wire ``llm_node`` with fakes and count every expensive prompt input."""
    captured: dict = {}
    calls = {"builder": 0, "prompt_variables": 0, "skills_catalog": 0}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                provider="default",
                model="model-x",
                system_prompt="Cached prompt",
                tools=["skill"],
            )

    def _fake_builder(**kwargs):
        calls["builder"] += 1
        return f"Prompt build #{calls['builder']}"

    async def _fake_prompt_variables():
        calls["prompt_variables"] += 1
        return {}

    async def _fake_skills_catalog(**kwargs):
        calls["skills_catalog"] += 1
        return "<available_skills/>"

    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        names = list(override_tool_names) if override_tool_names else []
        return [SimpleNamespace(name=name) for name in names]

    monkeypatch.setattr(
        llm_node_module,
        "get_resolved_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(llm_node_module, "build_llm_system_prompt", _fake_builder)
    monkeypatch.setattr(
        llm_node_module,
        "get_runtime_prompt_variable_values",
        _fake_prompt_variables,
    )
    monkeypatch.setattr(
        llm_node_module.ToolResolver,
        "aresolve_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.skills.get_effective_skills_catalog_xml",
        _fake_skills_catalog,
    )

    async def _run(*, agent_name: str = "cache-agent", thread_id: str = "thread-1"):
        return await llm_node_module.llm_node(
            {"messages": [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": thread_id}},
            SimpleNamespace(
                context=WorkflowContext(
                    agent_name=agent_name,
                    working_dir="D:/repo",
                    max_context_tokens=50000,
                    allowed_tool_names=["skill", "read_file"],
                )
            ),
        )

    return SimpleNamespace(run=_run, calls=calls, captured=captured)


@pytest.mark.asyncio
async def test_repeated_turns_reuse_the_cached_system_prompt(cached_llm_node) -> None:
    await cached_llm_node.run()
    await cached_llm_node.run()
    await cached_llm_node.run()

    assert cached_llm_node.calls == {
        "builder": 1,
        "prompt_variables": 1,
        "skills_catalog": 1,
    }
    assert cached_llm_node.captured["system_prompts"] == ["Prompt build #1"] * 3


@pytest.mark.asyncio
async def test_revision_bump_rebuilds_the_system_prompt(cached_llm_node) -> None:
    await cached_llm_node.run()

    bump_revision(PROMPT_VARIABLES_REVISION)
    await cached_llm_node.run()

    assert cached_llm_node.calls == {
        "builder": 2,
        "prompt_variables": 2,
        "skills_catalog": 2,
    }
    assert cached_llm_node.captured["system_prompts"] == [
        "Prompt build #1",
        "Prompt build #2",
    ]


@pytest.mark.asyncio
async def test_different_thread_does_not_reuse_another_threads_prompt(
    cached_llm_node,
) -> None:
    await cached_llm_node.run(thread_id="thread-1")
    await cached_llm_node.run(thread_id="thread-2")

    assert cached_llm_node.calls["builder"] == 2


@pytest.mark.asyncio
async def test_cache_disabled_rebuilds_every_turn(cached_llm_node, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.modules.workflows.system_prompt_cache.get_system_prompt_cache_ttl_seconds",
        lambda: 0,
    )

    await cached_llm_node.run()
    await cached_llm_node.run()

    assert cached_llm_node.calls["builder"] == 2
