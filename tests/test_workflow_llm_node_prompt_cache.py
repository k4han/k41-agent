"""Coverage for the system prompt cache wired into ``llm_node``."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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

    def bind_tools(self, tools, **kwargs):
        self._captured["model_kwargs"] = kwargs
        return self

    async def ainvoke(self, messages, config=None):
        self._captured.setdefault("system_prompts", []).append(messages[0].content)
        self._captured["messages"] = messages
        return AIMessage(content="ok")


def _fake_chat_model_factory(captured: dict):
    def _factory(model: str | None = None, *, provider_name: str | None = None):
        return SimpleNamespace(
            model=_FakeChatModel(captured),
            provider_name=provider_name or "default",
            provider_type="openai_compatible",
            model_name=model or "default-model",
            profile={"reasoning_effort_levels": ["low", "medium", "high"]} if model == "gpt-6.1-sol" else None,
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

    async def _run(*, agent_name: str = "cache-agent", thread_id: str = "thread-1", messages=None, model=None, reasoning_effort=None):
        return await llm_node_module.llm_node(
            {"messages": messages if messages is not None else [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": thread_id}},
            SimpleNamespace(
                context=WorkflowContext(
                    agent_name=agent_name,
                    working_dir="D:/repo",
                    max_context_tokens=50000,
                    allowed_tool_names=["skill", "read"],
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
            ),
        )

    return SimpleNamespace(run=_run, calls=calls, captured=captured)


@pytest.mark.asyncio
async def test_effort_changes_apply_per_turn_without_leaking_to_other_models(cached_llm_node):
    await cached_llm_node.run(model="gpt-5", reasoning_effort="low")
    assert cached_llm_node.captured["model_kwargs"] == {"reasoning_effort": "low"}
    await cached_llm_node.run(model="gpt-5", reasoning_effort="high")
    assert cached_llm_node.captured["model_kwargs"] == {"reasoning_effort": "high"}
    await cached_llm_node.run(model="gpt-6.1-sol", reasoning_effort="high")
    assert cached_llm_node.captured["model_kwargs"] == {"reasoning": {"effort": "high"}}
    await cached_llm_node.run(model="gpt-4.1", reasoning_effort="high")
    assert cached_llm_node.captured["model_kwargs"] == {}
    await cached_llm_node.run(model="gpt-5")
    assert cached_llm_node.captured["model_kwargs"] == {}


@pytest.mark.asyncio
async def test_llm_node_sends_agent_card_effort(cached_llm_node, monkeypatch):
    from agent.modules.agents.models import AgentConfig

    agent = AgentConfig(
        name="cache-agent", graph_type="react_agent", provider="default",
        model={"id": "gpt-5", "effort": "low"},
    )
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: agent))
    monkeypatch.setattr("agent.modules.workflows.model_effort.get_chat_model_selection", lambda **kwargs: ("default", "gpt-5"))
    await cached_llm_node.run()
    assert cached_llm_node.captured["model_kwargs"] == {"reasoning_effort": "low"}
    await cached_llm_node.run(reasoning_effort="high")
    assert cached_llm_node.captured["model_kwargs"] == {"reasoning_effort": "high"}
    await cached_llm_node.run(reasoning_effort="auto")
    assert cached_llm_node.captured["model_kwargs"] == {}
    await cached_llm_node.run(model="gpt-4.1")
    assert cached_llm_node.captured["model_kwargs"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key,effort,expected", [
    ("", "medium", "high"),
    ("test-key", "medium", "medium"),
    ("test-key", "max", None),
])
async def test_fallback_resets_effort_without_relaxing_primary_validation(
    cached_llm_node, monkeypatch, api_key, effort, expected,
):
    from importlib import import_module
    from agent.modules.providers.provider import ProviderConfig, ProviderType
    from agent.modules.providers.service import ProviderService

    resolver = import_module("agent.modules.providers.resolve_chat_model")
    providers = {
        "primary": ProviderConfig(
            name="primary", provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="", api_key=api_key, default_model="gpt-5",
        ),
        "backup": ProviderConfig(
            name="backup", provider_type=ProviderType.GOOGLE,
            base_url="", api_key="test-key", default_model="gemini-3-pro-preview",
        ),
    }
    settings = {
        "llm.fallback.provider": "backup",
        "llm.fallback.model": "gemini-3-pro-preview",
    }
    monkeypatch.setattr(resolver, "get_default_llm_settings", lambda: ("primary", "gpt-5"))
    monkeypatch.setattr(resolver, "get_config_service", lambda: SimpleNamespace(
        get=lambda key: None,
        get_str=lambda key, default="": settings.get(key, default),
    ))
    service = ProviderService(SimpleNamespace(
        get_provider=lambda name: providers["primary" if name == "default" else name],
        get_default_provider=lambda: providers["primary"],
    ))

    class Factory:
        def create(self, provider_config, model_config, key):
            cached_llm_node.captured["resolved_model"] = model_config.model_name
            return _FakeChatModel(cached_llm_node.captured)

    factory = Factory()
    service.register_factory(ProviderType.OPENAI_COMPATIBLE, factory)
    service.register_factory(ProviderType.GOOGLE, factory)
    monkeypatch.setattr(llm_node_module, "get_resolved_chat_model", lambda **kwargs:
        resolver.resolve_chat_model_info(service, **kwargs))
    resolver._get_cached_model.cache_clear()
    try:
        if expected is None:
            with pytest.raises(ValueError, match="Unsupported reasoning effort"):
                await cached_llm_node.run(model="gpt-5", reasoning_effort=effort)
            assert "model_kwargs" not in cached_llm_node.captured
        else:
            result = await cached_llm_node.run(model="gpt-5", reasoning_effort=effort)
            assert result["messages"][0].content == "ok"
            assert cached_llm_node.captured["model_kwargs"] == {"reasoning_effort": expected}
            assert cached_llm_node.captured["resolved_model"] == (
                "gpt-5" if api_key else "gemini-3-pro-preview"
            )
    finally:
        resolver._get_cached_model.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_llm_node_sends_coding_receipt_without_ui_diff(cached_llm_node, legacy):
    from agent.modules.tools.coding.models import ToolResult

    summary = "Changed source.txt: +1/-0; version=version-1"
    diff = "+submitted-content\n"
    result = ToolResult(content=summary + "\n" + diff if legacy else summary,
                        display_content=None if legacy else summary + "\n" + diff,
                        data={"path": "source.txt", "additions": 1, "deletions": 0,
                              "version": "version-1", "diff": diff})
    message = ToolMessage(content=result.content, name="write", tool_call_id="write-call",
                          artifact=result.model_dump(exclude={"content"}))
    await cached_llm_node.run(messages=[HumanMessage(content="Write the file"), message])
    sent = cached_llm_node.captured["messages"][-1]
    assert sent.content == summary and sent.artifact is None
    assert "submitted-content" not in str(sent.model_dump())
    assert message.artifact["data"]["diff"] == diff


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
