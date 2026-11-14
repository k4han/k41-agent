"""Bound dashboard message payloads while preserving checkpoint history semantics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.delivery.http.dashboard.routes import conversations
from agent.modules.admin_auth import get_current_admin
from agent.modules.conversations import history


def checkpoint(checkpoint_id, messages, parent_id=None):
    return SimpleNamespace(
        config={"configurable": {"checkpoint_id": checkpoint_id}},
        parent_config={"configurable": {"checkpoint_id": parent_id}} if parent_id else None,
        checkpoint={"id": checkpoint_id, "channel_values": {"messages": messages}},
    )


@pytest.fixture
def message_history(monkeypatch):
    messages = [
        message
        for index in range(126)
        for message in (
            HumanMessage(content=f"question-{index}", id=f"human-{index}"),
            AIMessage(content=f"answer-{index}", id=f"assistant-{index}"),
        )
    ]
    saved = checkpoint("saved", messages)
    checkpoints = {"saved": saved}
    state = SimpleNamespace(latest="saved")

    class Checkpointer:
        async def aget_tuple(self, config):
            selected = config["configurable"].get("checkpoint_id") or state.latest
            return checkpoints.get(selected)

        async def alist(self, config):
            for item in reversed(list(checkpoints.values())):
                yield item

    monkeypatch.setattr(history, "get_history_checkpointer", lambda: Checkpointer())
    return SimpleNamespace(messages=messages, checkpoints=checkpoints, state=state)


@pytest.mark.asyncio
async def test_pages_stay_on_selected_checkpoint_and_preserve_absolute_indices(message_history):
    latest = await history.get_thread_messages_page("thread", include_branch_metadata=True)
    assert len(latest["messages"]) == 100
    assert latest["messages"][0]["message_index"] == 152
    assert latest["messages"][-1]["message_index"] == 251
    assert latest["messages"][0]["source_checkpoint_id"] == "saved"
    assert latest["total_messages"] == 252
    assert latest["total_user_messages"] == 126
    assert latest["has_more"] is True
    assert latest["next_before"] == 152

    message_history.checkpoints["new"] = checkpoint("new", [
        *message_history.messages, HumanMessage(content="new question"), AIMessage(content="new answer"),
    ], "saved")
    message_history.state.latest = "new"
    middle = await history.get_thread_messages_page(
        "thread", checkpoint_id="saved", before=152, include_branch_metadata=True,
    )
    first = await history.get_thread_messages_page(
        "thread", checkpoint_id="saved", before=middle["next_before"], include_branch_metadata=True,
    )
    assert middle["active_checkpoint_id"] == first["active_checkpoint_id"] == "saved"
    assert middle["total_messages"] == 252
    assert middle["total_user_messages"] == first["total_user_messages"] == 126
    assert (await history.get_thread_messages_page("thread"))["total_user_messages"] == 127
    assert middle["next_before"] == 52
    assert first["has_more"] is False
    assert first["next_before"] is None
    combined = first["messages"] + middle["messages"] + latest["messages"]
    assert [message["id"] for message in combined] == [message.id for message in message_history.messages]
    assert [message["message_index"] for message in combined] == list(range(252))


@pytest.mark.asyncio
async def test_only_selected_messages_are_serialized_and_enriched(message_history, monkeypatch):
    from agent.modules import agent_runtime

    serialize = history._serialize_thread_messages
    visited = []

    def serialize_page(messages, **kwargs):
        result = serialize(messages, **kwargs)
        visited.extend(result)
        return result

    enrich = AsyncMock(side_effect=lambda messages, thread_id: messages)
    monkeypatch.setattr(history, "_serialize_thread_messages", serialize_page)
    monkeypatch.setattr(agent_runtime, "enrich_delegated_calls", enrich)
    page = await history.get_thread_messages_page("thread", limit=3, before=200)
    assert len(visited) == 3
    assert visited == page["messages"]
    enrich.assert_awaited_once_with(page["messages"], "thread")
    assert page["next_before"] == 197


@pytest.mark.asyncio
@pytest.mark.parametrize("before,expected_count,next_before", [(0, 0, None), (1, 1, None), (999, 100, 152)])
async def test_message_page_boundaries(message_history, before, expected_count, next_before):
    page = await history.get_thread_messages_page("thread", before=before)
    assert len(page["messages"]) == expected_count
    assert page["next_before"] == next_before
    assert page["total_user_messages"] == 126


@pytest.mark.asyncio
async def test_missing_checkpoint_and_empty_history(message_history):
    with pytest.raises(history.CheckpointNotFoundError):
        await history.get_thread_messages_page("thread", checkpoint_id="missing")
    message_history.state.latest = "missing"
    assert await history.get_thread_messages_page("thread") == {
        "messages": [], "active_checkpoint_id": "", "total_messages": 0,
        "total_user_messages": 0,
        "has_more": False, "next_before": None,
    }


@pytest.mark.asyncio
async def test_page_preserves_branch_and_tool_metadata(message_history):
    shared = [HumanMessage(content="first"), AIMessage(content="first answer")]
    original = checkpoint("original", [*shared, HumanMessage(content="original"), AIMessage(content="old")])
    edited = checkpoint("edited", [
        *shared, HumanMessage(content="edited"),
        AIMessage(content="", tool_calls=[{"id": "call", "name": "read_file", "args": {}}]),
        ToolMessage(content="file content", tool_call_id="call", name="read_file"),
        AIMessage(content="new"),
    ])
    message_history.checkpoints.clear()
    message_history.checkpoints.update(original=original, edited=edited)
    message_history.state.latest = "edited"
    page = await history.get_thread_messages_page("thread", limit=4, include_branch_metadata=True)
    user, call, result, answer = page["messages"]
    assert user["message_index"] == 2
    assert user["branch"]["total"] == 2
    assert {option["checkpoint_id"] for option in user["branch"]["options"]} == {"original", "edited"}
    assert call["tool_calls"][0]["id"] == result["tool_call_id"] == "call"
    assert result["content"] == "file content"
    assert answer["message_index"] == 5
    assert answer["source_checkpoint_id"] == "edited"


@pytest.mark.asyncio
async def test_internal_history_still_returns_all_messages(message_history):
    messages, selected = await history.get_thread_messages_payload("thread")
    assert len(messages) == 252
    assert selected == "saved"


@pytest.fixture
def history_client(message_history, monkeypatch):
    from agent.bootstrap import container as container_module

    monkeypatch.setattr(container_module, "require_active_container", lambda: SimpleNamespace(persistence_ready=False))
    monkeypatch.setattr(conversations, "get_conversation_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(conversations, "workspace_ref_for_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(conversations, "get_thread_resume_error", AsyncMock(return_value=None))
    monkeypatch.setattr("agent.modules.tools.pending_permission_requests", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(conversations.router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    with TestClient(app) as client:
        yield client


def test_http_history_defaults_to_bounded_latest_page(history_client):
    response = history_client.get("/dashboard-api/chat-history/thread")
    assert response.status_code == 200
    page = response.json()
    assert len(page["messages"]) == 100
    assert page["active_checkpoint_id"] == "saved"
    assert page["total_messages"] == 252
    assert page["total_user_messages"] == 126
    assert page["next_before"] == 152
    response = history_client.get("/dashboard-api/chat-history/thread", params={
        "checkpoint_id": page["active_checkpoint_id"], "before": page["next_before"], "limit": 25,
    })
    assert response.status_code == 200
    assert len(response.json()["messages"]) == 25
    assert response.json()["next_before"] == 127
    assert response.json()["total_user_messages"] == 126


def test_http_user_message_total_follows_selected_branch(history_client, message_history):
    message_history.checkpoints["branch"] = checkpoint("branch", [
        HumanMessage(content="branch question"), AIMessage(content="branch answer"),
    ])
    response = history_client.get("/dashboard-api/chat-history/thread", params={
        "checkpoint_id": "branch", "limit": 1,
    })
    assert response.status_code == 200
    assert response.json()["messages"][0]["role"] == "assistant"
    assert response.json()["total_user_messages"] == 1


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": -1}, {"limit": 501}, {"limit": "all"},
    {"before": -1, "checkpoint_id": "saved"}, {"before": "invalid", "checkpoint_id": "saved"},
    {"before": 100},
])
def test_http_history_rejects_invalid_pagination(history_client, params):
    assert history_client.get("/dashboard-api/chat-history/thread", params=params).status_code == 422


def test_http_history_missing_checkpoint_returns_404(history_client):
    response = history_client.get("/dashboard-api/chat-history/thread", params={"checkpoint_id": "missing"})
    assert response.status_code == 404
