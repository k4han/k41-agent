"""Coverage for the system prompt cache wired into ``llm_node``."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

import agent.modules.workflows.nodes.llm as llm_node_module
from agent.modules.skills import get_effective_skills_catalog_xml as real_skills_catalog_xml
from agent.modules.tools.catalog_context import build_tool_catalogs
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
        self._captured.setdefault("bound_tools", []).append(tools)
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
        captured["skills_catalog_xml"] = kwargs["skills_catalog_xml"]
        kwargs["sections"].update(build_tool_catalogs(
            tools=kwargs["tools"], agent_name=kwargs["agent_name"], catalog=kwargs["catalog"],
            skills_catalog_xml=kwargs["skills_catalog_xml"] or "<available_skills/>",
        ))
        return f"Prompt build #{calls['builder']}"

    async def _fake_prompt_variables(workspace, *, thread_id=""):
        calls["prompt_variables"] += 1
        return {}

    async def _fake_skills_catalog(**kwargs):
        calls["skills_catalog"] += 1
        return "<available_skills/>"

    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        names = list(override_tool_names) if override_tool_names else []
        return [SimpleNamespace(name=name, description="Test tool", tool_call_schema={"type": "object", "properties": {}}) for name in names]

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

    async def _run(
        *, agent_name: str = "cache-agent", thread_id: str = "thread-1",
        messages=None, model=None, reasoning_effort=None,
        allowed_tool_names=None, working_dir="D:/repo",
    ):
        return await llm_node_module.llm_node(
            {"messages": messages if messages is not None else [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": thread_id}},
            SimpleNamespace(
                context=WorkflowContext(
                    agent_name=agent_name,
                    working_dir=working_dir,
                    context_compact_threshold=75,
                    allowed_tool_names=["skill", "read"] if allowed_tool_names is None else allowed_tool_names,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
            ),
        )

    return SimpleNamespace(run=_run, calls=calls, captured=captured)


@pytest.mark.asyncio
async def test_project_secret_names_refresh_outside_the_cached_prompt(cached_llm_node, monkeypatch):
    contexts = iter(("\n<project_secrets>TOKEN_ONE</project_secrets>", "\n<project_secrets>TOKEN_TWO</project_secrets>"))

    async def names(workspace, *, thread_id=""):
        return next(contexts)

    monkeypatch.setattr("agent.modules.projects.secrets.project_secret_context", names)
    await cached_llm_node.run()
    await cached_llm_node.run()
    assert cached_llm_node.calls["builder"] == 1
    first, second = cached_llm_node.captured["system_prompts"]
    assert "TOKEN_ONE" in first and "TOKEN_ONE" not in second and "TOKEN_TWO" in second


@pytest.mark.asyncio
async def test_stale_project_secret_binding_does_not_block_chat(cached_llm_node, isolated_container):
    from unittest.mock import AsyncMock
    from agent.modules.projects.workspaces import ProjectWorkspaceError

    isolated_container._persistence_ready = True
    isolated_container._project_workspace_service = SimpleNamespace(
        find_binding=AsyncMock(side_effect=ProjectWorkspaceError("Runtime replaced.", "stale_runtime")),
    )
    result = await cached_llm_node.run(allowed_tool_names=[])
    assert result["messages"][-1].content == "ok"
    assert "<project_secrets>" not in cached_llm_node.captured["system_prompts"][0]


@pytest.mark.asyncio
async def test_pool_rebuilds_context_and_effort_and_commits_only_successful_history(cached_llm_node, monkeypatch):
    from agent.modules.providers.model_pools import ModelPoolService
    from agent.modules.providers.models import ResolvedChatModel
    from agent.modules.providers.provider import ProviderConfig, ProviderType
    from langchain_core.messages import RemoveMessage

    pool_value = {"members": [{"provider": "primary", "model": "first"}, {"provider": "backup", "model": "second"}], "retry_count": 0}
    service = ModelPoolService(
        SimpleNamespace(get=lambda key: pool_value, get_all=lambda: {"llm.model_pools.small": pool_value}),
        SimpleNamespace(get_provider=lambda name: ProviderConfig(name=name, provider_type=ProviderType.OPENAI_COMPATIBLE,
                                                                 base_url="", api_key="test", default_model="m")),
    )
    prepared, calls = [], []

    class Model:
        def __init__(self, name):
            self.name = name

        def bind_tools(self, tools, **kwargs):
            calls.append((self.name, kwargs))
            return self

        async def ainvoke(self, messages, config=None):
            if self.name == "primary":
                raise TimeoutError("Unavailable")
            assert messages[-1].content == "prepared-backup"
            assert config["metadata"]["pool_name"] == "small"
            return AIMessage(content="success")

    def resolve(name, member, temperature=None):
        effort = "low" if member.provider == "primary" else "high"
        return ResolvedChatModel(model=Model(member.provider), provider_name=member.provider,
                                 provider_type="openai_compatible", model_name=member.model, pool_name=name,
                                 context_window=200000 if effort == "low" else 32000,
                                 profile={"reasoning_effort_levels": [effort], "reasoning_effort_default": effort})

    async def prepare(history, *, resolved, context, **kwargs):
        assert history[0].content == "original"
        assert not context.channel_trim_applied
        context.channel_trim_applied = True
        prepared.append((resolved.provider_name, resolved.context_window))
        return [HumanMessage(content="prepared-" + resolved.provider_name)], [RemoveMessage(id="old-" + resolved.provider_name)]

    monkeypatch.setattr(service, "_resolve", resolve)
    monkeypatch.setattr("agent.modules.providers.model_pools.get_model_pool_service", lambda: service)
    monkeypatch.setattr(llm_node_module, "get_resolved_chat_model", lambda **kwargs: service.preview("small"))
    monkeypatch.setattr(llm_node_module, "prepare_model_context", prepare)
    result = await cached_llm_node.run(messages=[HumanMessage(content="original")], allowed_tool_names=["read"])
    assert prepared == [("primary", 200000), ("backup", 32000)]
    assert calls == [("primary", {"reasoning_effort": "low"}), ("backup", {"reasoning_effort": "high"})]
    assert [message.id for message in result["messages"][:-1]] == ["old-backup"]
    assert result["messages"][-1].content == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_names", [["skill", "read"], ["read"]])
async def test_invalid_skill_directory_does_not_block_llm_turns(
    cached_llm_node, isolated_container, tmp_path, monkeypatch, tool_names,
):
    from agent.modules.skills.repository import FilesystemSkillRepository

    repository = FilesystemSkillRepository(tmp_path / "global")
    repository.create_skill(
        "global-skill",
        "---\nname: global-skill\ndescription: Global.\n---\nGlobal instructions.\n",
    )
    workspace = tmp_path / "workspace"
    local_directory = workspace / "custom" / "skills" / "local-skill"
    local_directory.mkdir(parents=True)
    (local_directory / "SKILL.md").write_text(
        "---\nname: local-skill\ndescription: Local.\n---\nLocal instructions.\n",
        encoding="utf-8",
    )
    configured_directory = ["custom\\skills"]
    config_service = isolated_container.config_service
    original_get_str = config_service.get_str

    def get_str(key, default=""):
        if key == "skills.repository_dir":
            return configured_directory[0]
        return original_get_str(key, default)

    monkeypatch.setattr(config_service, "get_str", get_str)
    monkeypatch.setattr("agent.modules.skills._get_repository", lambda: repository)
    monkeypatch.setattr("agent.modules.skills.get_effective_skills_catalog_xml", real_skills_catalog_xml)
    kwargs = {"allowed_tool_names": tool_names, "working_dir": str(workspace)}

    result = await cached_llm_node.run(**kwargs)
    assert result["messages"][0].content == "ok"
    if "skill" in tool_names:
        assert "<name>global-skill</name>" in cached_llm_node.captured["skills_catalog_xml"]
        assert "local-skill" not in cached_llm_node.captured["skills_catalog_xml"]
    else:
        assert cached_llm_node.captured["skills_catalog_xml"] is None
    await cached_llm_node.run(**kwargs)
    assert cached_llm_node.calls["builder"] == 1

    configured_directory[0] = "custom/skills"
    await cached_llm_node.run(**kwargs)
    assert cached_llm_node.calls["builder"] == 2
    if "skill" in tool_names:
        assert "<name>global-skill</name>" in cached_llm_node.captured["skills_catalog_xml"]
        assert "<name>local-skill</name>" in cached_llm_node.captured["skills_catalog_xml"]


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


@pytest.mark.asyncio
async def test_cached_catalogs_reach_tool_descriptions_once_and_refresh_after_revision(cached_llm_node, monkeypatch):
    from agent.shared.infrastructure.revisions import SKILLS_REVISION

    current = ["<available_skills><skill><name>first</name></skill></available_skills>"]

    async def catalog(**kwargs):
        return current[0]

    monkeypatch.setattr("agent.modules.skills.get_effective_skills_catalog_xml", catalog)
    await cached_llm_node.run()
    await cached_llm_node.run()
    assert cached_llm_node.calls["builder"] == 1
    for tools in cached_llm_node.captured["bound_tools"]:
        description = next(tool.description for tool in tools if tool.name == "skill")
        assert description.count("<available_skills>") == 1
        assert "<name>first</name>" in description
    current[0] = current[0].replace("first", "second")
    bump_revision(SKILLS_REVISION)
    await cached_llm_node.run()
    latest = next(tool.description for tool in cached_llm_node.captured["bound_tools"][-1] if tool.name == "skill")
    assert "<name>second</name>" in latest and "<name>first</name>" not in latest
    assert cached_llm_node.calls["builder"] == 2
