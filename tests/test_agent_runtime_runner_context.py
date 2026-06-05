import base64
import importlib
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Command

import agent.modules.conversations as conversations_module
from agent.modules.agent_runtime import runner as runner_module
from agent.modules.tools.langchain.utility_tools.plan_mode import (
    PLAN_MODE_TOOL_NAME,
    PLAN_REVIEW_INTERRUPT_TYPE,
)


@pytest.mark.asyncio
async def test_clear_agent_session_closes_shell_sessions_and_deletes_thread_tree(
    monkeypatch,
):
    shell_manager_module = importlib.import_module(
        "agent.modules.tools.langchain.shell_tools.session_manager"
    )
    workflows_module = importlib.import_module("agent.modules.workflows")
    workspaces_module = importlib.import_module("agent.modules.workspaces")
    calls = []

    class FakeSessionManager:
        def close_thread_sessions(self, thread_id: str) -> int:
            calls.append(("close_shell", thread_id))
            return 1

    async def fake_delete_workflow_thread_tree(thread_id: str):
        calls.append(("delete_tree", thread_id))

    async def fake_delete_thread_workspace(thread_id: str):
        calls.append(("delete_workspace", thread_id))

    monkeypatch.setattr(shell_manager_module, "session_manager", FakeSessionManager())
    monkeypatch.setattr(
        workspaces_module,
        "delete_thread_workspace",
        fake_delete_thread_workspace,
    )
    monkeypatch.setattr(
        workflows_module,
        "delete_workflow_thread_tree",
        fake_delete_workflow_thread_tree,
    )

    await runner_module.clear_agent_session(
        platform="api",
        user_id="dashboard",
        channel_id="thread-1",
    )

    assert calls == [
        ("close_shell", "api_dashboard_thread-1"),
        ("delete_workspace", "api_dashboard_thread-1"),
        ("delete_tree", "api_dashboard_thread-1"),
    ]


@pytest.mark.asyncio
async def test_record_conversation_thread_schedules_title_generation_for_user_thread(
    monkeypatch,
):
    calls = {}

    async def fake_get_conversation_thread(thread_id: str):
        calls["get"] = thread_id
        return None

    async def fake_upsert_conversation_thread(**kwargs):
        calls["upsert"] = kwargs

    def fake_schedule_conversation_title_generation(**kwargs):
        calls["schedule_title"] = kwargs

    monkeypatch.setattr(
        conversations_module,
        "get_conversation_thread",
        fake_get_conversation_thread,
    )
    monkeypatch.setattr(
        conversations_module,
        "upsert_conversation_thread",
        fake_upsert_conversation_thread,
    )
    monkeypatch.setattr(
        conversations_module,
        "schedule_conversation_title_generation",
        fake_schedule_conversation_title_generation,
    )

    await runner_module._record_conversation_thread(
        thread_id="api:dashboard:thread-1",
        agent_name="default",
        title="How do I debug this login issue?",
        attachments=[{"name": "auth.py", "kind": "text"}],
    )

    assert calls["get"] == "api:dashboard:thread-1"
    assert calls["upsert"] == {
        "thread_id": "api:dashboard:thread-1",
        "agent_name": "default",
        "title": "How do I debug this login issue?",
        "kind": "user",
    }
    assert calls["schedule_title"] == {
        "thread_id": "api:dashboard:thread-1",
        "title": "How do I debug this login issue?",
        "attachments": [{"name": "auth.py", "kind": "text"}],
    }


@pytest.mark.asyncio
async def test_record_conversation_thread_preserves_existing_manual_title(monkeypatch):
    calls = {"schedule_title": 0}

    async def fake_get_conversation_thread(thread_id: str):
        return {"thread_id": thread_id, "title": "Manual title"}

    async def fake_upsert_conversation_thread(**kwargs):
        calls["upsert"] = kwargs

    def fake_schedule_conversation_title_generation(**kwargs):
        calls["schedule_title"] += 1

    monkeypatch.setattr(
        conversations_module,
        "get_conversation_thread",
        fake_get_conversation_thread,
    )
    monkeypatch.setattr(
        conversations_module,
        "upsert_conversation_thread",
        fake_upsert_conversation_thread,
    )
    monkeypatch.setattr(
        conversations_module,
        "schedule_conversation_title_generation",
        fake_schedule_conversation_title_generation,
    )

    await runner_module._record_conversation_thread(
        thread_id="api:dashboard:thread-1",
        agent_name="default",
        title="New request",
    )

    assert calls["schedule_title"] == 0
    assert calls["upsert"] == {
        "thread_id": "api:dashboard:thread-1",
        "agent_name": "default",
        "title": "",
        "kind": "user",
    }


@pytest.mark.asyncio
async def test_record_conversation_thread_skips_generation_for_background_thread(monkeypatch):
    calls = {"get": 0, "schedule_title": 0}

    async def fake_get_conversation_thread(thread_id: str):
        calls["get"] += 1
        return None

    async def fake_upsert_conversation_thread(**kwargs):
        calls["upsert"] = kwargs

    def fake_schedule_conversation_title_generation(**kwargs):
        calls["schedule_title"] += 1

    monkeypatch.setattr(
        conversations_module,
        "get_conversation_thread",
        fake_get_conversation_thread,
    )
    monkeypatch.setattr(
        conversations_module,
        "upsert_conversation_thread",
        fake_upsert_conversation_thread,
    )
    monkeypatch.setattr(
        conversations_module,
        "schedule_conversation_title_generation",
        fake_schedule_conversation_title_generation,
    )

    await runner_module._record_conversation_thread(
        thread_id="task_dashboard_123",
        agent_name="default",
        title="Run background task",
    )

    assert calls["get"] == 0
    assert calls["schedule_title"] == 0
    assert calls["upsert"] == {
        "thread_id": "task_dashboard_123",
        "agent_name": "default",
        "title": "Run background task",
        "kind": "background",
    }


@pytest.mark.asyncio
async def test_run_agent_omits_context_for_graph_without_context_schema(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="research_chain",
                max_context_tokens=1234,
                tools=["list_dir"],
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
                tools=["list_dir"],
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
async def test_run_agent_resume_payload_marks_run_as_resume(monkeypatch):
    captured: dict = {}
    recorded: list[dict] = []

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_dir"],
            )

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            yield {"messages": [AIMessage(content="revised done", id="ai-final")]}

    async def _fake_record_conversation_thread(**kwargs):
        recorded.append(kwargs)

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
    monkeypatch.setattr(
        runner_module,
        "_record_conversation_thread",
        _fake_record_conversation_thread,
    )

    chunks = [
        chunk
        async for chunk in runner_module.run_agent(
            user_input="",
            thread_id="thread-1",
            agent_name="default",
            resume_payload={"action": "revise", "feedback": "Make it shorter."},
        )
    ]

    assert chunks == ["revised done"]
    assert isinstance(captured["payload"], Command)
    assert captured["payload"].resume == {
        "action": "revise",
        "feedback": "Make it shorter.",
    }
    assert recorded == []


@pytest.mark.asyncio
async def test_run_agent_stream_builds_multimodal_user_message(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_dir"],
            )

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
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

    attachments = [
        {
            "name": "snippet.py",
            "mime_type": "text/x-python",
            "size": 1,
            "kind": "text",
            "content": "print('hi')",
        },
        {
            "name": "screen.png",
            "mime_type": "image/png",
            "size": 1,
            "kind": "image",
            "base64": "YWJjZA==",
        },
    ]

    events = [
        event
        async for event in runner_module.run_agent_stream(
            user_input="Review attachments",
            thread_id="thread-1",
            agent_name="default",
            attachments=attachments,
        )
    ]

    assert events == [{"type": "final", "content": "done"}]
    user_message = captured["payload"]["messages"][0]
    assert isinstance(user_message, HumanMessage)
    assert user_message.content == [
        {"type": "text", "text": "Review attachments"},
        {
            "type": "text",
            "text": (
                "Attached text file: snippet.py\n"
                "MIME type: text/x-python\n"
                "Size: 11 bytes\n\n"
                "print('hi')"
            ),
        },
        {
            "type": "text",
            "text": "Attached image: screen.png\nMIME type: image/png\nSize: 4 bytes",
        },
        {"type": "image", "base64": "YWJjZA==", "mime_type": "image/png"},
    ]
    assert user_message.additional_kwargs["attachments"] == [
        {
            "name": "snippet.py",
            "mime_type": "text/x-python",
            "size": 11,
            "kind": "text",
        },
        {
            "name": "screen.png",
            "mime_type": "image/png",
            "size": 4,
            "kind": "image",
        },
    ]


def test_build_run_params_rejects_spoofed_text_attachment_size():
    with pytest.raises(ValueError, match="too large"):
        runner_module.build_run_params(
            platform="api",
            user_id="alice",
            user_input="Review this",
            attachments=[
                {
                    "name": "large.txt",
                    "mime_type": "text/plain",
                    "size": 1,
                    "kind": "text",
                    "content": "a" * (runner_module.MAX_TEXT_ATTACHMENT_BYTES + 1),
                }
            ],
        )


def test_build_run_params_rejects_spoofed_image_attachment_size():
    image_bytes = b"x" * (runner_module.MAX_IMAGE_ATTACHMENT_BYTES + 1)

    with pytest.raises(ValueError, match="too large"):
        runner_module.build_run_params(
            platform="api",
            user_id="alice",
            user_input="Review this",
            attachments=[
                {
                    "name": "large.png",
                    "mime_type": "image/png",
                    "size": 1,
                    "kind": "image",
                    "base64": base64.b64encode(image_bytes).decode("ascii"),
                }
            ],
        )


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
                tools=["list_dir"],
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
                tools=["list_dir"],
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
                tools=["list_dir"],
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
                tools=["list_dir"],
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
async def test_run_agent_stream_skips_checkpoint_messages_before_current_user(monkeypatch):
    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_dir"],
            )

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            user_message = payload["messages"][0]
            checkpoint_messages = [
                HumanMessage(content="old request", id="old-user"),
                AIMessage(content="old response", id="old-ai"),
                user_message,
            ]
            yield ("values", {"messages": checkpoint_messages})
            yield (
                "values",
                {
                    "messages": [
                        *checkpoint_messages,
                        AIMessage(content="current response", id="current-ai"),
                    ]
                },
            )

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
            user_input="new request",
            thread_id="thread-1",
            agent_name="default",
        )
    ]

    assert events == [{"type": "final", "content": "current response"}]


@pytest.mark.asyncio
async def test_run_agent_stream_emits_tool_call_and_result(monkeypatch):
    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_dir"],
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
                                "name": "list_dir",
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
                        name="list_dir",
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
            "name": "list_dir",
            "args": {"path": "."},
        },
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "list_dir",
            "content": "README.md\nagent/",
        },
        {"type": "final", "content": "done"},
    ]


@pytest.mark.asyncio
async def test_run_agent_stream_emits_text_attached_to_tool_call(monkeypatch):
    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_dir"],
            )

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            yield {
                "messages": [
                    AIMessage(
                        content="I will inspect the files.",
                        id="ai-tool",
                        tool_calls=[
                            {
                                "id": "call-1",
                                "name": "list_dir",
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
                        name="list_dir",
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
        {"type": "final", "content": "I will inspect the files."},
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "list_dir",
            "args": {"path": "."},
        },
        {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "list_dir",
            "content": "README.md\nagent/",
        },
        {"type": "final", "content": "done"},
    ]


@pytest.mark.asyncio
async def test_run_agent_stream_resume(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_dir"],
            )

    class _FakeGraph:
        async def aget_state(self, config):
            class FakeState:
                values = {
                    "messages": [
                        HumanMessage(content="old-user-message", id="old-user"),
                        AIMessage(content="old-ai-message", id="old-ai")
                    ]
                }
            return FakeState()

        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            # Yield events as if continuing from the previous run
            yield ("values", {
                "messages": [
                    HumanMessage(content="old-user-message", id="old-user"),
                    AIMessage(content="old-ai-message", id="old-ai"),
                    AIMessage(content="resumed-ai-message", id="new-ai")
                ]
            })

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
            user_input="",
            thread_id="thread-1",
            agent_name="default",
            resume=True,
        )
    ]

    assert events == [{"type": "final", "content": "resumed-ai-message"}]
    assert captured["payload"] is None


@pytest.mark.asyncio
async def test_run_agent_stream_emits_plan_review_interrupt(monkeypatch):
    plan = "1. Inspect\n2. Implement"

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=[PLAN_MODE_TOOL_NAME],
            )

    class _FakeInterrupt:
        id = "interrupt-1"
        value = {
            "type": PLAN_REVIEW_INTERRUPT_TYPE,
            "tool_call_id": "call-plan",
            "plan": plan,
        }

    class _FakeGraph:
        async def astream(self, payload, **kwargs):
            yield (
                "values",
                {
                    "messages": [
                        AIMessage(
                            content="",
                            id="ai-plan",
                            tool_calls=[
                                {
                                    "name": PLAN_MODE_TOOL_NAME,
                                    "args": {"plan": plan},
                                    "id": "call-plan",
                                }
                            ],
                        )
                    ],
                    "__interrupt__": (_FakeInterrupt(),),
                },
            )

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
            user_input="make a plan",
            thread_id="thread-1",
            agent_name="default",
        )
    ]

    assert events == [
        {
            "type": PLAN_REVIEW_INTERRUPT_TYPE,
            "tool_call_id": "call-plan",
            "interrupt_id": "interrupt-1",
            "plan": plan,
        }
    ]


@pytest.mark.asyncio
async def test_run_agent_stream_resume_payload_switches_agent(monkeypatch):
    captured: dict = {}
    updated: list[tuple[str, str]] = []

    class _FakeCatalog:
        def get_agent(self, name: str):
            if name not in {"planner", "worker"}:
                return None
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["read_file"],
            )

        def get_agent_card(self, name: str):
            if name == "planner":
                return SimpleNamespace(
                    hidden=False,
                    valid=True,
                    plan_approval_targets=["worker"],
                )
            if name == "worker":
                return SimpleNamespace(hidden=False, valid=True)
            return None

    class _FakeGraph:
        async def aget_state(self, config):
            return SimpleNamespace(
                values={
                    "messages": [
                        HumanMessage(content="old-user", id="old-user"),
                        AIMessage(content="", id="old-ai"),
                    ]
                }
            )

        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            yield (
                "values",
                {
                    "messages": [
                        HumanMessage(content="old-user", id="old-user"),
                        AIMessage(content="", id="old-ai"),
                        AIMessage(content="worker done", id="new-ai"),
                    ]
                },
            )

    async def _fake_update_thread_agent(thread_id: str, agent_name: str):
        updated.append((thread_id, agent_name))

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
    monkeypatch.setattr(runner_module, "_update_thread_agent", _fake_update_thread_agent)

    events = [
        event
        async for event in runner_module.run_agent_stream(
            user_input="",
            thread_id="thread-1",
            agent_name="planner",
            resume_payload={"action": "approve", "target_agent": "worker"},
        )
    ]

    assert events == [{"type": "final", "content": "worker done"}]
    assert isinstance(captured["payload"], Command)
    assert captured["payload"].resume == {
        "action": "approve",
        "target_agent": "worker",
    }
    assert captured["kwargs"]["context"]["agent_name"] == "worker"
    assert updated == [("thread-1", "worker")]


@pytest.mark.asyncio
async def test_run_agent_stream_resume_payload_rejects_disallowed_plan_target(
    monkeypatch,
):
    class _FakeCatalog:
        def get_agent(self, name: str):
            if name not in {"planner", "worker", "reviewer"}:
                return None
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["read_file"],
            )

        def get_agent_card(self, name: str):
            if name == "planner":
                return SimpleNamespace(
                    hidden=False,
                    valid=True,
                    plan_approval_targets=["reviewer"],
                )
            if name in {"worker", "reviewer"}:
                return SimpleNamespace(hidden=False, valid=True)
            return None

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    with pytest.raises(ValueError, match="not allowed as a plan approval target"):
        [
            event
            async for event in runner_module.run_agent_stream(
                user_input="",
                thread_id="thread-1",
                agent_name="planner",
                resume_payload={"action": "approve", "target_agent": "worker"},
            )
        ]


def test_plan_resume_rejects_missing_source_agent_card():
    class _FakeCatalog:
        def get_agent(self, name: str):
            if name == "worker":
                return SimpleNamespace(
                    graph_type="react_agent",
                    max_context_tokens=1234,
                    tools=["read_file"],
                )
            return None

        def get_agent_card(self, name: str):
            if name == "worker":
                return SimpleNamespace(hidden=False, valid=True)
            return None

    resume_payload = runner_module._normalize_plan_resume_payload(
        {"action": "approve", "target_agent": "worker"}
    )

    with pytest.raises(ValueError, match="cannot be validated for plan approval"):
        runner_module._resolve_agent_name_for_resume(
            _FakeCatalog(),
            "planner",
            resume_payload,
            source_agent_name="planner",
        )


@pytest.mark.asyncio
async def test_run_agent_edit_stream_forks_from_parent_and_preserves_attachments(
    monkeypatch,
):
    captured: dict = {}
    original_message = HumanMessage(
        content=[
            {"type": "text", "text": "Review this"},
            {"type": "text", "text": "Attached text file: notes.txt\n\nold"},
        ],
        id="user-1",
        additional_kwargs={
            "attachments": [
                {
                    "name": "notes.txt",
                    "mime_type": "text/plain",
                    "size": 3,
                    "kind": "text",
                }
            ]
        },
    )

    class _FakeCatalog:
        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                max_context_tokens=1234,
                tools=["list_dir"],
            )

    class _FakeGraph:
        async def aget_state_history(self, config):
            yield SimpleNamespace(
                config={
                    "configurable": {
                        "thread_id": "thread-1",
                        "checkpoint_id": "source-1",
                    }
                },
                parent_config={
                    "configurable": {
                        "thread_id": "thread-1",
                        "checkpoint_id": "parent-1",
                    }
                },
                values={"messages": [original_message]},
            )

        async def aget_state(self, config):
            captured["state_config"] = config
            return SimpleNamespace(values={"messages": []})

        async def astream(self, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            edited = payload["messages"][0]
            yield ("values", {"messages": [edited, AIMessage(content="edited response", id="ai-1")]})

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
        async for event in runner_module.run_agent_edit_stream(
            user_input="Updated request",
            thread_id="thread-1",
            agent_name="default",
            message_index=0,
            source_checkpoint_id="source-1",
        )
    ]

    assert events == [{"type": "final", "content": "edited response"}]
    assert captured["kwargs"]["config"]["configurable"]["checkpoint_id"] == "parent-1"
    edited_message = captured["payload"]["messages"][0]
    assert edited_message.id == "user-1"
    assert edited_message.content[0]["text"] == "Updated request"
    assert edited_message.content[1]["text"] == "Attached text file: notes.txt\n\nold"
    assert edited_message.additional_kwargs == original_message.additional_kwargs

