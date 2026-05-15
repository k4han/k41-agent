from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agent.modules.agent_runtime import runner as runner_module


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
        "agent.modules.agents.get_catalog_service",
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
async def test_run_agent_passes_model_override_to_context(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_files"],
            )

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            yield {"messages": [AIMessage(content="done")]}

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
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
            provider="openai-main",
            model="direct-model",
        )
    ]

    assert chunks == ["done"]
    assert captured["kwargs"]["context"]["provider"] == "openai-main"
    assert captured["kwargs"]["context"]["model"] == "direct-model"


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
        "agent.modules.agents.get_catalog_service",
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


@pytest.mark.asyncio
async def test_run_agent_extracts_last_text_from_structured_content(monkeypatch):
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
            yield {
                "messages": [
                    AIMessage(
                        content=[
                            {"type": "thinking", "thinking": "internal"},
                            {"type": "text", "text": "visible response"},
                        ]
                    )
                ]
            }

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
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

    assert chunks == ["visible response"]


@pytest.mark.asyncio
async def test_run_agent_stream_extracts_last_text_from_structured_content(monkeypatch):
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
            yield {
                "messages": [
                    AIMessage(
                        content=[
                            {"type": "thinking", "thinking": "internal"},
                            {"type": "text", "text": "visible response"},
                        ],
                        id="msg-structured",
                    )
                ]
            }

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
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

    assert events == [{"type": "final", "content": "visible response"}]


@pytest.mark.asyncio
async def test_run_agent_stream_emits_message_chunks_and_final(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_files"],
            )

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            captured["kwargs"] = kwargs
            yield ("messages", (AIMessageChunk(content="hel"), {"langgraph_node": "llm"}))
            yield ("messages", (AIMessageChunk(content=" "), {"langgraph_node": "llm"}))
            yield (
                "messages",
                (
                    AIMessageChunk(content=[{"type": "text", "text": "lo"}]),
                    {"langgraph_node": "llm"},
                ),
            )
            yield ("values", {"messages": [AIMessage(content="hel lo", id="ai-final")]})

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
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

    assert captured["kwargs"]["stream_mode"] == ["messages", "values"]
    assert events == [
        {"type": "message", "content": "hel"},
        {"type": "message", "content": " "},
        {"type": "message", "content": "lo"},
        {"type": "final", "content": "hel lo"},
    ]


@pytest.mark.asyncio
async def test_run_agent_stream_emits_tool_call_and_result(monkeypatch):
    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_files"],
            )

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            yield {
                "messages": [
                    AIMessage(
                        content="",
                        id="ai-tool",
                        tool_calls=[
                            {
                                "id": "call-1",
                                "name": "list_files",
                                "args": {"path": "."},
                            }
                        ],
                    )
                ]
            }
            yield {
                "messages": [
                    ToolMessage(
                        content="README.md\nagent/",
                        tool_call_id="call-1",
                        name="list_files",
                        id="tool-result",
                    )
                ]
            }
            yield {"messages": [AIMessage(content="done", id="ai-final")]}

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
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

    assert events == [
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "list_files",
            "args": {"path": "."},
        },
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "list_files",
            "content": "README.md\nagent/",
        },
        {"type": "final", "content": "done"},
    ]
