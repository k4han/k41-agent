"""Chat history loads independent data concurrently using the selected checkpoint."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agent.delivery.http.dashboard.routes import conversations


@pytest.mark.asyncio
@pytest.mark.parametrize("persistence_ready", [False, True])
@pytest.mark.parametrize("has_metadata", [False, True])
async def test_chat_history_queries_overlap_and_use_resolved_checkpoint(monkeypatch, persistence_ready, has_metadata):
    from agent.bootstrap import container as container_module
    from agent.modules import tools

    thread_id = "api_dashboard_123"
    started = [set(), set()]
    ready = [asyncio.Event(), asyncio.Event()]
    expected = [3, 3 if persistence_ready else 2]

    async def synchronize(phase, name):
        started[phase].add(name)
        if len(started[phase]) == expected[phase]:
            ready[phase].set()
        await asyncio.wait_for(ready[phase].wait(), timeout=1)

    async def messages(thread, *, checkpoint_id, include_branch_metadata):
        assert thread == thread_id
        assert checkpoint_id == "requested-checkpoint"
        assert include_branch_metadata is True
        await synchronize(0, "messages")
        return [{"role": "user", "content": "hello"}], "resolved-checkpoint"

    async def metadata(thread):
        assert thread == thread_id
        await synchronize(0, "metadata")
        return {"agent_name": "reviewer"} if has_metadata else None

    async def workspace(thread, *, include_default):
        assert thread == thread_id
        assert include_default is False
        await synchronize(0, "workspace")
        return SimpleNamespace(model_dump=lambda: {"locator": "workspace"})

    async def permissions(thread, *, checkpoint_id):
        assert thread == thread_id
        assert checkpoint_id == "resolved-checkpoint"
        await synchronize(1, "permissions")
        return [{"type": "permission_request"}]

    async def resume_error(thread, *, agent_name, checkpoint_id):
        assert thread == thread_id
        assert agent_name == ("reviewer" if has_metadata else "default")
        assert checkpoint_id == "resolved-checkpoint"
        await synchronize(1, "resume_error")
        return {"code": "execution_failed"}

    async def handoff(thread, owner):
        assert thread == thread_id
        assert owner == "admin:test_admin"
        await synchronize(1, "handoff")
        return {"id": "handoff-1"}

    container = SimpleNamespace(persistence_ready=persistence_ready)
    if persistence_ready:
        container.browser_service = SimpleNamespace(owned_handoff=handoff)
    monkeypatch.setattr(container_module, "require_active_container", lambda: container)
    monkeypatch.setattr(conversations, "get_thread_messages_payload", messages)
    monkeypatch.setattr(conversations, "get_conversation_thread", metadata)
    monkeypatch.setattr(conversations, "workspace_ref_for_thread", workspace)
    monkeypatch.setattr(conversations, "get_thread_resume_error", resume_error)
    monkeypatch.setattr(tools, "pending_permission_requests", permissions)

    payload = await asyncio.wait_for(conversations.get_chat_thread_messages(
        thread_id, checkpoint_id="requested-checkpoint", admin="test_admin",
    ), timeout=3)

    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["active_checkpoint_id"] == "resolved-checkpoint"
    assert payload["workspace"] == {"locator": "workspace"}
    assert payload["pending_requests"] == [{"type": "permission_request"}]
    assert payload["resume_error"] == {"code": "execution_failed"}
    if persistence_ready:
        assert payload["pending_browser_handoffs"] == [{"id": "handoff-1"}]
    else:
        assert "pending_browser_handoffs" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status,detail", [
    (conversations.CheckpointNotFoundError(), 404, "Checkpoint not found."),
    (conversations.ConversationHistoryUnavailableError("History unavailable."), 503, "History unavailable."),
])
async def test_parallel_chat_history_preserves_checkpoint_errors(monkeypatch, error, status, detail):
    async def messages(*args, **kwargs):
        raise error

    async def no_metadata(*args, **kwargs):
        return None

    monkeypatch.setattr(conversations, "get_thread_messages_payload", messages)
    monkeypatch.setattr(conversations, "get_conversation_thread", no_metadata)
    monkeypatch.setattr(conversations, "workspace_ref_for_thread", no_metadata)
    with pytest.raises(HTTPException) as caught:
        await conversations.get_chat_thread_messages("api_dashboard_123", checkpoint_id="missing", admin="admin")
    assert caught.value.status_code == status
    assert caught.value.detail == detail
