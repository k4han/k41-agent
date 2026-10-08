"""Agent effort defaults, runtime overrides, and workflow request propagation."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.modules.agents.models import AgentConfig
from agent.modules.workflows.model_effort import get_workflow_reasoning_effort_kwargs


@pytest.fixture(autouse=True)
def configured_model_selection(monkeypatch):
    monkeypatch.setattr(
        "agent.modules.workflows.model_effort.get_chat_model_selection",
        lambda **kwargs: (kwargs["provider_name"], kwargs["model"]),
    )


def _agent() -> AgentConfig:
    return AgentConfig(
        name="sample", graph_type="react_agent", provider="anthropic",
        model={"id": "claude-opus-5-5", "effort": "low"},
    )


def _resolved(**overrides):
    return SimpleNamespace(
        **{"provider_name": "anthropic", "provider_type": "anthropic",
           "model_name": "claude-opus-5-5", "used_fallback": False,
           **overrides},
    )


@pytest.mark.parametrize("effort, overrides, expected", [
    (None, {}, "low"),
    ("high", {}, "high"),
    ("auto", {}, "medium"),
    (None, {"model_name": "claude-opus-4-6"}, "high"),
    (None, {"provider_name": "other"}, "medium"),
    (None, {"used_fallback": True}, "medium"),
    ("high", {"used_fallback": True}, "medium"),
    ("auto", {"used_fallback": True}, "medium"),
    ("auto", {"profile": {"reasoning_effort_levels": ["low", "high"]}}, None),
    (None, {"profile": {"reasoning_effort_levels": []}}, None),
    (None, {"profile": {}}, None),
])
def test_workflow_effort_precedence(effort, overrides, expected):
    kwargs = get_workflow_reasoning_effort_kwargs(
        SimpleNamespace(reasoning_effort=effort), _agent(), _resolved(**overrides),
    )
    assert kwargs == ({"reasoning_effort": expected} if expected else {})


def test_workflow_effort_rejects_unsupported_card_default():
    agent = _agent()
    agent.reasoning_effort = "invalid"
    with pytest.raises(ValueError, match="Unsupported reasoning effort"):
        get_workflow_reasoning_effort_kwargs(SimpleNamespace(reasoning_effort=None), agent, _resolved())


@pytest.mark.parametrize("provider", ["", "default", "anthropic"])
@pytest.mark.parametrize("model", ["", "default", "provider default", "claude-opus-5-5"])
@pytest.mark.parametrize("override", [None, "model", "provider"])
def test_card_defaults_match_resolved_identity(monkeypatch, provider, model, override):
    from importlib import import_module
    from agent.modules.providers.provider import ProviderConfig, ProviderType
    from agent.modules.providers.service import ProviderService

    resolver = import_module("agent.modules.providers.resolve_chat_model")
    monkeypatch.setattr(resolver, "get_default_llm_settings", lambda: ("anthropic", "claude-opus-5-5"))
    configured_provider = ProviderConfig(
        name="anthropic", provider_type=ProviderType.ANTHROPIC,
        default_model="claude-opus-4-6", api_key="", base_url="",
    )
    service = ProviderService(SimpleNamespace(
        get_provider=lambda name: configured_provider,
        get_default_provider=lambda: configured_provider,
    ))

    def selection(**kwargs):
        config, model_name = resolver.resolve_chat_model_selection(service, **kwargs)
        return config.name, model_name

    monkeypatch.setattr("agent.modules.workflows.model_effort.get_chat_model_selection", selection)
    agent = AgentConfig(
        name="sample", graph_type="react_agent", provider=provider,
        model=model, reasoning_effort="low",
    )
    expected_provider, expected_model = selection(provider_name=provider, model=model)
    resolved = _resolved(provider_name=expected_provider, model_name=expected_model)
    if override == "model":
        resolved.model_name = "different-model"
    elif override == "provider":
        resolved.provider_name = "different-provider"
    # A leaked card effort would fail validation for either override.
    resolved.profile = {
        "reasoning_effort_levels": ["low", "high"] if override is None else ["medium", "high"],
        "reasoning_effort_default": "high",
    }
    assert get_workflow_reasoning_effort_kwargs(SimpleNamespace(reasoning_effort=None), agent, resolved) == {
        "reasoning_effort": "low" if override is None else "high",
    }


def test_unavailable_card_default_does_not_fail_runtime_override(monkeypatch):
    def unavailable(**kwargs):
        raise ValueError("Provider is unavailable")

    monkeypatch.setattr("agent.modules.workflows.model_effort.get_chat_model_selection", unavailable)
    agent = _agent()
    agent.provider = "default"
    assert get_workflow_reasoning_effort_kwargs(SimpleNamespace(reasoning_effort=None), agent, _resolved()) == {
        "reasoning_effort": "medium",
    }


@pytest.mark.parametrize("provider_name", ["anthropic", "ANTHROPIC-MAIN", "anthropic-main"])
def test_card_effort_resolves_provider_aliases(monkeypatch, provider_name):
    from importlib import import_module
    from agent.modules.providers.provider import ProviderConfig, ProviderType
    from agent.modules.providers.repository import ConfigProviderRepository
    from agent.modules.providers.service import ProviderService

    resolver = import_module("agent.modules.providers.resolve_chat_model")
    provider = ProviderConfig(
        name="anthropic-main", provider_type=ProviderType.ANTHROPIC,
        default_model="claude-opus-5-5", api_key="test-key", base_url="",
    )
    repository = ConfigProviderRepository()
    monkeypatch.setattr(repository, "_load", lambda: ({"anthropic_main": provider}, "anthropic_main"))
    service = ProviderService(repository)
    monkeypatch.setattr(resolver, "get_default_llm_settings", lambda: (provider.name, provider.default_model))

    def selection(**kwargs):
        config, model = resolver.resolve_chat_model_selection(service, **kwargs)
        return config.name, model

    monkeypatch.setattr("agent.modules.workflows.model_effort.get_chat_model_selection", selection)
    agent = _agent()
    agent.provider = provider_name
    context = SimpleNamespace(reasoning_effort=None)
    assert get_workflow_reasoning_effort_kwargs(context, agent, _resolved(provider_name=provider.name)) == {
        "reasoning_effort": "low",
    }
    assert get_workflow_reasoning_effort_kwargs(context, agent, _resolved(provider_name="other")) == {
        "reasoning_effort": "medium",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("node", ["router", "research", "summarize"])
async def test_workflows_send_agent_effort(monkeypatch, node):
    from agent.modules.workflows.graphs import research, router
    from agent.modules.workflows.run_config import WorkflowContext

    captured = {}

    class Model:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages, config=None, **kwargs):
            captured.update(kwargs)
            if node == "router":
                return SimpleNamespace(selected_agent="worker")
            return AIMessage(content="Summary")

    agent = _agent()
    resolved = _resolved(model=Model())
    context = WorkflowContext(agent_name=agent.name)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: agent))
    monkeypatch.setattr(router if node == "router" else research, "get_resolved_chat_model", lambda **kwargs: resolved)
    if node == "router":
        await router._route_agent_name(
            "hi", {"worker": agent}, "{agent_options}\n{user_input}", agent.name,
            context=context, agent_config=agent,
        )
    else:
        func = research._research_node if node == "research" else research._summarize_node
        await func({"messages": [HumanMessage(content="hi")]}, {}, SimpleNamespace(context=context))
    assert captured == {"reasoning_effort": "low"}
