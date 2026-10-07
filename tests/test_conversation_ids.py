from dataclasses import replace
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import Column, MetaData, String, Table, create_engine, select
from sqlalchemy.exc import IntegrityError

from agent.delivery.cli.session import CLISession
from agent.modules.agent_runtime.runner import _record_conversation_thread, track_active_session
from agent.shared.thread_ids import SessionManager, canonical_thread_id, resolve_thread_id, storage_thread_id
from agent.modules.conversations import get_conversation_thread, upsert_conversation_thread
from agent.modules.conversations.migrations import migrate_conversation_ids
from agent.modules.conversations.service import create_thread_id
from agent.modules.tools.builtin.schedule.schedule import _parse_runtime_thread_id
from agent.modules.tools.coding.contracts import InvocationContext
from agent.modules.tools.coding.storage import conversation_key, output_relative_path
from agent.modules.tools.runtime import thread_storage
from agent.modules.usage import UsageEventInput, get_usage_service
from agent.modules.usage.service import load_usage_context
from agent.modules.workspaces import get_thread_workspace_ref, remember_thread_workspace_ref


def test_new_conversation_ids_are_opaque_and_unique():
    ids = {create_thread_id(platform="api", user_id="dashboard") for _ in range(100)}
    assert len(ids) == 100
    assert all(re.fullmatch(r"[a-f0-9]{32}", value) for value in ids)
    stable = SessionManager.make_thread_id("telegram", "123", "456")
    assert re.fullmatch(r"[a-f0-9]{32}", stable)
    assert stable == SessionManager.make_thread_id("telegram", "123", "456")
    assert stable != SessionManager.make_thread_id("discord", "123", "456")
    assert stable != SessionManager.make_thread_id("telegram", "124", "456")
    assert stable != SessionManager.make_thread_id("telegram", "123", "457")


def test_cli_resumes_full_code_without_reencoding():
    session = CLISession()
    original = session.thread_id
    assert session.reset_thread() != original
    assert session.use_thread(original) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_events", [False, True])
async def test_dashboard_runs_keep_stored_identity_and_approval_support(isolated_container, monkeypatch, stream_events):
    from agent.modules.agent_runtime import runner

    await isolated_container.initialize_persistence()
    thread_id = create_thread_id(platform="api", user_id="dashboard")
    captured = {}

    class FakeGraph:
        async def astream(self, payload, **kwargs):
            captured.update(kwargs["config"])
            yield {"messages": [AIMessage(content="Done")]}

    card = SimpleNamespace(graph_type="react_agent", tools=[], max_context_tokens=1000)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: card))
    monkeypatch.setattr(runner, "get_workflow_graph", lambda name: FakeGraph())
    monkeypatch.setattr(runner, "make_run_context", lambda **kwargs: kwargs)
    try:
        await upsert_conversation_thread(thread_id=thread_id, platform="telegram", user_id="123", channel_id="456", title="Manual title")
        run = runner.run_agent_stream if stream_events else runner.run_agent
        results = [item async for item in run(
            user_input="Continue", thread_id=thread_id,
            usage_context={"platform": "api", "user_id": "dashboard"},
        )]
        assert results
        assert captured["configurable"]["thread_id"] == thread_id
        assert captured["configurable"]["approval_supported"] is True
        assert captured["metadata"]["usage_context"]["platform"] == "telegram"
        assert captured["metadata"]["usage_context"]["user_id"] == "123"
    finally:
        await isolated_container.close_persistence()


@pytest.mark.asyncio
async def test_opaque_identity_is_persisted_and_reused(isolated_container, monkeypatch):
    import agent.modules.conversations as conversations

    await isolated_container.initialize_persistence()
    monkeypatch.setattr(conversations, "schedule_conversation_title_generation", lambda **kwargs: None)
    thread_id = create_thread_id(platform="api", user_id="dashboard")
    identity = {"platform": "api", "user_id": "dashboard", "channel_id": ""}
    try:
        await _record_conversation_thread(thread_id=thread_id, agent_name="default", title="First message", usage_context=identity)
        stored = await get_conversation_thread(thread_id)
        assert {key: stored[key] for key in identity} == identity
        await upsert_conversation_thread(thread_id=thread_id, agent_name="worker")
        stored = await get_conversation_thread(thread_id)
        assert stored["platform"] == "api"
        assert stored["user_id"] == "dashboard"
        loaded = await load_usage_context(thread_id)
        with track_active_session(thread_id, "worker", usage_context=loaded.to_dict()) as session_id:
            session = isolated_container.active_session_registry.get(session_id)
            assert session["platform"] == "api"
            assert session["user_id"] == "dashboard"
        runtime = SimpleNamespace(config={"configurable": {"thread_id": thread_id}, "metadata": {"usage_context": loaded.to_dict()}})
        assert _parse_runtime_thread_id(runtime) == ("api", "dashboard")
        child_context = await load_usage_context(f"{thread_id}:sub:worker:123")
        assert child_context.platform == "api"
        assert child_context.root_thread_id == thread_id
        assert child_context.thread_id.endswith(":sub:worker:123")
    finally:
        await isolated_container.close_persistence()


@pytest.mark.asyncio
async def test_migration_preserves_history_workspaces_usage_and_files(isolated_container, tmp_path, monkeypatch):
    await isolated_container.initialize_persistence()
    old = "api_dashboard_91c23fc6b24a"
    child = f"{old}:sub:worker:123"
    new = canonical_thread_id(old)
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    original_storage = thread_storage.thread_storage_root(old)
    original_storage.mkdir(parents=True)
    (original_storage / "note.txt").write_text("Saved note", encoding="utf-8")
    old_context = InvocationContext("default", str(tmp_path), old)
    old_key = conversation_key(old)
    old_output = output_relative_path(old_context, "a" * 32)
    checkpointer = isolated_container.checkpointer
    try:
        await upsert_conversation_thread(thread_id=old, title="Saved title", provider="provider", model="model")
        await remember_thread_workspace_ref(old, {"backend": "local", "locator": str(tmp_path), "metadata": {"temp": True, "thread_id": old}})
        for thread_id in (old, child):
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {"messages": [HumanMessage(content="Saved message")]}
            config = await checkpointer.aput(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                checkpoint, {"source": "input", "step": 0, "parents": {}}, {},
            )
            await checkpointer.aput_writes(config, [("pending", "Saved write")], "task")
        await get_usage_service().record_event(UsageEventInput(
            thread_id=child, root_thread_id=old, platform="api", user_id="dashboard", channel_id="91c23fc6b24a",
            agent_name="worker", provider_name="provider", model_name="model", call_kind="agent",
            internal=False, has_usage_metadata=True, input_tokens=10, output_tokens=5, total_tokens=15,
        ))
        await isolated_container.close_persistence()
        await isolated_container.initialize_persistence()
        assert resolve_thread_id(old) == new
        assert resolve_thread_id(child) == f"{new}:sub:worker:123"
        assert storage_thread_id(new) == old
        assert SessionManager.parse_thread_id(new) == ("api", "dashboard", "91c23fc6b24a")
        assert migrate_conversation_ids(isolated_container.database_url) == {old: new}
        stored = await get_conversation_thread(old)
        assert stored["thread_id"] == new
        assert stored["title"] == "Saved title"
        assert stored["provider"] == "provider"
        workspace = await get_thread_workspace_ref(old)
        assert workspace.locator == str(tmp_path)
        assert workspace.metadata["thread_id"] == new
        for thread_id in (new, f"{new}:sub:worker:123"):
            saved = await isolated_container.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
            assert saved.checkpoint["channel_values"]["messages"][0].content == "Saved message"
            assert saved.pending_writes[0][2] == "Saved write"
        usage = await get_usage_service().get_thread_usage(old)
        assert usage["thread_id"] == new
        assert usage["total_tokens"] == 15
        assert thread_storage.thread_storage_root(new) == original_storage
        assert (thread_storage.thread_storage_root(new) / "note.txt").read_text(encoding="utf-8") == "Saved note"
        assert conversation_key(new) == old_key
        migrated_context = replace(old_context, thread_id=new)
        assert migrated_context.owner == old_context.owner
        assert output_relative_path(migrated_context, "a" * 32) == old_output
    finally:
        await isolated_container.close_persistence()


def test_migration_rolls_back_if_a_canonical_id_already_exists(tmp_path):
    old = "api_dashboard_conflict"
    new = canonical_thread_id(old)
    database_url = f"sqlite:///{(tmp_path / 'collision.db').as_posix()}"
    engine = create_engine(database_url)
    table = Table("conversation_threads", MetaData(), Column("thread_id", String, primary_key=True), Column("title", String))
    table.create(engine)
    try:
        with engine.begin() as conn:
            conn.execute(table.insert(), [{"thread_id": old, "title": old}, {"thread_id": new, "title": "Other conversation"}])
        with pytest.raises(IntegrityError):
            migrate_conversation_ids(database_url)
        with engine.connect() as conn:
            assert set(conn.execute(select(table.c.thread_id)).scalars()) == {old, new}
    finally:
        engine.dispose()
