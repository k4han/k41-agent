from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from agent.modules.conversations import service as conversation_service


class _FakeCatalog:
    def __init__(self, agent_config=None) -> None:
        self.agent_config = agent_config

    def get_agent(self, name: str):
        assert name == conversation_service.CONVERSATION_TITLE_AGENT_NAME
        return self.agent_config


class _FakeChatModel:
    def __init__(self, response: object) -> None:
        self.response = response
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        if isinstance(self.response, Exception):
            raise self.response
        return AIMessage(content=self.response)


def _title_agent_config():
    return SimpleNamespace(
        provider="default",
        model="",
        system_prompt="Return a concise title.",
    )


@pytest.mark.asyncio
async def test_generate_conversation_title_uses_builtin_agent_prompt(monkeypatch):
    fake_model = _FakeChatModel('"Fix login bug"\nextra ignored')

    monkeypatch.setattr(
        conversation_service,
        "get_catalog_service",
        lambda: _FakeCatalog(_title_agent_config()),
    )
    monkeypatch.setattr(
        conversation_service,
        "get_chat_model",
        lambda **kwargs: fake_model,
    )

    title = await conversation_service.generate_conversation_title(
        first_user_message="Can you fix the login bug?",
        attachments=[
            {
                "name": "auth.py",
                "kind": "text",
                "mime_type": "text/x-python",
            }
        ],
    )

    assert title == "Fix login bug"
    assert fake_model.messages[0].content == "Return a concise title."
    assert "Can you fix the login bug?" in fake_model.messages[1].content
    assert "auth.py" in fake_model.messages[1].content


@pytest.mark.asyncio
async def test_generate_conversation_title_falls_back_on_model_error(monkeypatch):
    monkeypatch.setattr(
        conversation_service,
        "get_catalog_service",
        lambda: _FakeCatalog(_title_agent_config()),
    )
    monkeypatch.setattr(
        conversation_service,
        "get_chat_model",
        lambda **kwargs: _FakeChatModel(RuntimeError("boom")),
    )

    title = await conversation_service.generate_conversation_title(
        first_user_message="Investigate slow dashboard tests",
    )

    assert title == "Investigate slow dashboard tests"


@pytest.mark.asyncio
async def test_generate_conversation_title_falls_back_without_agent(monkeypatch):
    monkeypatch.setattr(
        conversation_service,
        "get_catalog_service",
        lambda: _FakeCatalog(None),
    )

    title = await conversation_service.generate_conversation_title(
        first_user_message="Plan database migration",
    )

    assert title == "Plan database migration"


@pytest.mark.asyncio
async def test_schedule_conversation_title_generation_updates_current_title(monkeypatch):
    calls = {}
    generation_started = asyncio.Event()
    release_generation = asyncio.Event()

    async def fake_generate_conversation_title(**kwargs):
        calls["generate"] = kwargs
        generation_started.set()
        await release_generation.wait()
        return "Debug login issue"

    async def fake_update_conversation_thread_title_if_current(**kwargs):
        calls["update_title"] = kwargs

    monkeypatch.setattr(
        conversation_service,
        "generate_conversation_title",
        fake_generate_conversation_title,
    )
    monkeypatch.setattr(
        conversation_service,
        "update_conversation_thread_title_if_current",
        fake_update_conversation_thread_title_if_current,
    )

    task = conversation_service.schedule_conversation_title_generation(
        thread_id="task_dashboard_123",
        title="How do I debug this login issue?",
        attachments=[{"name": "auth.py", "kind": "text"}],
    )

    await asyncio.wait_for(generation_started.wait(), timeout=1)
    assert calls["generate"] == {
        "first_user_message": "How do I debug this login issue?",
        "attachments": [{"name": "auth.py", "kind": "text"}],
    }
    release_generation.set()
    await asyncio.wait_for(task, timeout=1)

    assert calls["update_title"] == {
        "thread_id": "task_dashboard_123",
        "title": "Debug login issue",
        "current_titles": [
            "task_dashboard_123",
            "How do I debug this login issue?",
        ],
    }
