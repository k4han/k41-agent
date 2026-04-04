from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from agent.modules.agent_runtime.application import runner as runner_module


@pytest.mark.asyncio
async def test_run_agent_omits_context_for_graph_without_context_schema(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="research_chain",
                max_context_tokens=1234,
                tools=["list_files"],
            )

    class _FakeGraph:
        context_schema = None

        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            yield {"messages": [AIMessage(content="done")]}

    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(runner_module, "get_workflow_graph", lambda name: _FakeGraph())
    monkeypatch.setattr(runner_module, "make_run_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        runner_module,
        "make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    chunks = [
        chunk
        async for chunk in runner_module.run_agent(
            user_input="hi",
            thread_id="thread-1",
            agent_name="default",
        )
    ]

    assert chunks == ["done"]
    assert "context" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_run_agent_stream_omits_context_for_graph_without_context_schema(
    monkeypatch,
):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="research_chain",
                max_context_tokens=1234,
                tools=["list_files"],
            )

    class _FakeGraph:
        context_schema = None

        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            yield {"messages": [AIMessage(content="stream-done", id="msg-1")]}

    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(runner_module, "get_workflow_graph", lambda name: _FakeGraph())
    monkeypatch.setattr(runner_module, "make_run_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        runner_module,
        "make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    events = [
        event
        async for event in runner_module.run_agent_stream(
            user_input="hi",
            thread_id="thread-1",
            agent_name="default",
        )
    ]

    assert events == [{"type": "final", "content": "stream-done"}]
    assert "context" not in captured["kwargs"]