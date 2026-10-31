import asyncio
import copy
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import MetaData

import agent.modules.conversations.forking as forking
from agent.delivery.http.dashboard.routes.conversations import router
from agent.delivery.http.dashboard.routes.generated_images import resolve_storage_file
from agent.modules.agent_runtime import get_active_session_registry
from agent.modules.conversations import get_thread_messages_payload, upsert_conversation_thread
from agent.modules.conversations.history import forkable_message_indices
from agent.modules.conversations.repository import get_conversation_thread_repository
from agent.modules.tools.runtime import thread_storage
from agent.modules.workflows.state.base import BaseState
from agent.modules.workspaces import (
    WorkspaceRef,
    bind_workspace_ref,
    create_temp_workspace,
    derive_workspace_scope,
    get_thread_workspace_repository,
    remember_thread_workspace_ref,
)
from agent.shared.infrastructure.db.engine import get_sqlite_conn_string


@pytest_asyncio.fixture
async def fork_setup(isolated_container, tmp_path, monkeypatch):
    import agent.modules.workspaces.service as workspace_service

    monkeypatch.setattr(workspace_service, "temp_workspace_root", lambda: tmp_path / "temp")
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    from agent.modules.agent_runtime import BackgroundTaskRecord
    from agent.modules.conversations import ConversationThread, ConversationRunError
    from agent.modules.projects import Project, ProjectWorkspace, WorkspaceRuntime
    from agent.modules.workspaces import ThreadWorkspace
    from agent.shared.infrastructure.db.engine import initialize_async_engine

    # Use a fresh schema without starting unrelated runtime services.
    metadata = MetaData()
    for model in (ConversationThread, ConversationRunError, BackgroundTaskRecord, ThreadWorkspace, Project, ProjectWorkspace, WorkspaceRuntime):
        model.__table__.to_metadata(metadata)
    await initialize_async_engine(metadata=metadata, container=isolated_container)
    await isolated_container.initialize_checkpointer()
    isolated_container._persistence_ready = True
    calls = []

    async def respond(state):
        calls.append("model")
        last = state["messages"][-1]
        if isinstance(last, HumanMessage) and "tools" in str(last.content):
            return {"messages": [AIMessage(
                content="Checking files", id=f"call-{len(calls)}",
                tool_calls=[{"id": f"tool-{len(calls)}", "name": "read", "args": {}}],
            )]}
        return {"messages": [AIMessage(content=f"Answer {len(calls)}", id=f"answer-{len(calls)}")]}

    async def tool(state):
        calls.append("tool")
        return {"messages": [ToolMessage(content="Read complete", tool_call_id=state["messages"][-1].tool_calls[0]["id"])]}

    builder = StateGraph(BaseState)
    builder.add_node("llm", respond)
    builder.add_node("tool", tool)
    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", lambda state: "tool" if state["messages"][-1].tool_calls else END)
    builder.add_edge("tool", "llm")
    graph = builder.compile(checkpointer=isolated_container._checkpointer)
    monkeypatch.setattr(forking, "get_workflow_graph", lambda name: graph)
    monkeypatch.setattr(forking, "get_catalog_service", lambda: SimpleNamespace(
        get_agent=lambda name: SimpleNamespace(graph_type="test_fork"),
    ))
    monkeypatch.setattr(forking, "get_background_task_manager", lambda: SimpleNamespace(get_by_thread_id=lambda tid: None))
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(container=isolated_container, graph=graph, builder=builder, calls=calls, client=client)
    await isolated_container.close_persistence()


async def source_thread(setup, *, title="abc", thread_id="a" * 32, workspace=None):
    await upsert_conversation_thread(
        thread_id=thread_id, title=title, platform="api", user_id="dashboard",
        agent_name="default", provider="provider-a", model="model-a",
    )
    workspace = workspace or bind_workspace_ref(await create_temp_workspace(thread_id))
    await remember_thread_workspace_ref(thread_id, workspace)
    config = {"configurable": {"thread_id": thread_id}}
    await setup.graph.ainvoke({"messages": [HumanMessage(content="First")], "todos": [{"content": "First task", "status": "pending"}]}, config)
    return thread_id, workspace, config


async def fork_target(thread_id, index=-1):
    messages, _ = await get_thread_messages_payload(thread_id, include_branch_metadata=True)
    return [message for message in messages if message["role"] == "assistant" and "source_checkpoint_id" in message][index]


@pytest.mark.asyncio
async def test_fork_old_answer_copies_current_temp_files_and_persists(fork_setup):
    setup = fork_setup
    source_id, binding, config = await source_thread(setup)
    await setup.graph.ainvoke({"messages": [HumanMessage(
        content=[{"type": "text", "text": "Use tools"}, {"type": "image", "base64": "aW1hZ2U="}],
        additional_kwargs={"attachments": [{"name": "image.png", "kind": "image", "mime_type": "image/png", "size": 5}]},
    )]}, config)
    selected = await fork_target(source_id)
    expected = copy.deepcopy((await setup.graph.aget_state(config)).values)
    await setup.graph.ainvoke({"messages": [HumanMessage(content="Later request")]}, config)
    source_root = Path(binding.execution.locator)
    (source_root / "notes.txt").write_text("Current files", encoding="utf-8")
    thread_storage.ingest_attachment_file("input.txt", b"uploaded", binding.scope, source_root)
    thread_storage.save_generated_image("picture.png", b"image", binding.scope, source_root)
    calls_before = list(setup.calls)

    response = await setup.client.post(f"/dashboard-api/chat-history/{source_id}/fork", json={
        "source_checkpoint_id": selected["source_checkpoint_id"], "message_index": selected["message_index"],
    })
    assert response.status_code == 201, response.text
    fork = response.json()
    assert fork["thread_id"] != source_id
    assert fork["title"] == "abc (2)"
    assert (fork["agent_name"], fork["provider"], fork["model"]) == ("default", "provider-a", "model-a")
    assert setup.calls == calls_before
    assert [message["role"] for message in fork["messages"]] == ["user", "assistant", "user", "assistant", "tool", "assistant"]
    assert fork["messages"][2]["attachments"][0]["base64"] == "aW1hZ2U="
    assert fork["pending_requests"] == []
    assert fork["resume_error"] is None
    assert fork["workspace"]["project_id"] is None
    assert fork["workspace"]["execution"]["metadata"]["temp"] is True
    target_root = Path(fork["workspace"]["execution"]["locator"])
    assert target_root != source_root
    assert (target_root / "notes.txt").read_text() == "Current files"
    assert (target_root / ".k41-agent/uploads/input.txt").read_bytes() == b"uploaded"
    (target_root / "notes.txt").write_text("Branch only", encoding="utf-8")
    assert (source_root / "notes.txt").read_text() == "Current files"
    fork_config = {"configurable": {"thread_id": fork["thread_id"]}}
    state = await setup.graph.aget_state(fork_config)
    assert state.values == expected
    assert state.next == () and state.tasks == ()

    # Deleting the source must not break files served under the new thread id.
    await setup.container._checkpointer.adelete_thread(source_id)
    await get_thread_workspace_repository().delete(source_id)
    await get_conversation_thread_repository().delete(source_id)
    shutil.rmtree(source_root)
    shutil.rmtree(thread_storage.workspace_storage_root(binding.scope))
    image = await resolve_storage_file("picture.png", thread_id=fork["thread_id"])
    assert image.read_bytes() == b"image"
    assert (target_root / ".k41-agent/uploads/input.txt").read_bytes() == b"uploaded"

    async with AsyncSqliteSaver.from_conn_string(get_sqlite_conn_string(setup.container.database_url)) as saver:
        reopened = setup.builder.compile(checkpointer=saver)
        persisted = await reopened.aget_state(fork_config)
        assert persisted.values == expected
        assert persisted.next == () and persisted.tasks == ()
        await reopened.ainvoke({"messages": [HumanMessage(content="Continue branch")]}, fork_config)
        assert len((await reopened.aget_state(fork_config)).values["messages"]) == len(expected["messages"]) + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("message_index,new_text", [(0, "Revised first request"), (2, "Use tools")])
async def test_forked_prompts_can_be_edited_or_regenerated_after_reopening(fork_setup, monkeypatch, message_index, new_text):
    from agent.modules.agent_runtime import runner

    setup = fork_setup
    source_id, _, config = await source_thread(setup)
    await setup.graph.ainvoke({"messages": [HumanMessage(
        content="Use tools",
        additional_kwargs={"attachments": [{"name": "notes.txt", "kind": "text", "mime_type": "text/plain", "size": 4}]},
    )]}, config)
    source_state = await setup.graph.aget_state(config)
    selected = await fork_target(source_id)
    calls_before = list(setup.calls)
    fork = await forking.fork_conversation_thread(
        source_id, source_checkpoint_id=selected["source_checkpoint_id"], message_index=selected["message_index"],
    )
    assert setup.calls == calls_before
    prompt = fork["messages"][message_index]
    fork_config = {"configurable": {"thread_id": fork["thread_id"]}}
    copied = (await setup.graph.aget_state(fork_config)).values["messages"]

    async with AsyncSqliteSaver.from_conn_string(get_sqlite_conn_string(setup.container.database_url)) as saver:
        graph = setup.builder.compile(checkpointer=saver)
        monkeypatch.setattr(runner, "get_workflow_graph", lambda name: graph)
        monkeypatch.setattr("agent.modules.agents.get_catalog_service", forking.get_catalog_service)
        events = [event async for event in runner.run_agent_edit_stream(
            user_input=new_text, thread_id=fork["thread_id"], agent_name="default", workspace=fork["workspace"],
            message_index=prompt["message_index"], source_checkpoint_id=prompt["source_checkpoint_id"],
        )]
        updated = (await graph.aget_state(fork_config)).values["messages"]

    assert any(event["type"] == "final" for event in events)
    assert updated[:message_index] == copied[:message_index]
    assert updated[message_index].content == new_text
    assert updated[message_index].additional_kwargs == copied[message_index].additional_kwargs
    assert len(updated) == message_index + (4 if new_text == "Use tools" else 2)
    assert updated[-1].id != copied[-1].id
    assert (await setup.graph.aget_state(config)).values == source_state.values


@pytest.mark.asyncio
async def test_fork_numbering_and_concurrent_sources_share_temp_group(fork_setup):
    source_id, _, _ = await source_thread(fork_setup)
    target = await fork_target(source_id)
    first = await forking.fork_conversation_thread(source_id, source_checkpoint_id=target["source_checkpoint_id"], message_index=target["message_index"])
    forked_target = await fork_target(first["thread_id"])
    results = await asyncio.gather(
        forking.fork_conversation_thread(source_id, source_checkpoint_id=target["source_checkpoint_id"], message_index=target["message_index"]),
        forking.fork_conversation_thread(first["thread_id"], source_checkpoint_id=forked_target["source_checkpoint_id"], message_index=forked_target["message_index"]),
    )
    assert sorted(result["title"] for result in results) == ["abc (3)", "abc (4)"]


def test_title_length_and_large_suffixes():
    original = "a" * 255
    second = forking.next_fork_title(original, [original])
    assert second == "a" * 251 + " (2)"
    assert forking.next_fork_title(original, [second]) == "a" * 251 + " (3)"
    assert forking.next_fork_title("abc (9)", ["abc (12)"]) == "abc (13)"
    assert forking.next_fork_title("abc", ["abcd (50)"]) == "abc (2)"


@pytest.mark.asyncio
async def test_project_fork_keeps_project_workspace_and_numbering_separate(fork_setup, tmp_path):
    from agent.modules.projects import get_project_workspace_service

    root = tmp_path / "project"
    root.mkdir()
    (root / "file.txt").write_text("Shared project")
    source_id, _, _ = await source_thread(fork_setup, workspace=bind_workspace_ref(WorkspaceRef(locator=str(root))))
    projects = get_project_workspace_service()
    binding = await projects.resolve_thread(source_id)
    assert binding.project_id is not None and binding.project_workspace_id is not None
    target = await fork_target(source_id)
    fork = await forking.fork_conversation_thread(source_id, source_checkpoint_id=target["source_checkpoint_id"], message_index=target["message_index"])
    assert fork["workspace"] == binding.model_dump()
    assert (await projects.resolve_thread(fork["thread_id"])).project_workspace_id == binding.project_workspace_id
    assert (root / "file.txt").read_text() == "Shared project"
    temp_id, _, _ = await source_thread(fork_setup, thread_id="b" * 32)
    temp_target = await fork_target(temp_id)
    temp_fork = await forking.fork_conversation_thread(temp_id, source_checkpoint_id=temp_target["source_checkpoint_id"], message_index=temp_target["message_index"])
    assert temp_fork["title"] == fork["title"] == "abc (2)"
    projects.switcher.switching.add(binding.project_workspace_id)
    response = await fork_setup.client.post(f"/dashboard-api/chat-history/{source_id}/fork", json={
        "source_checkpoint_id": target["source_checkpoint_id"], "message_index": target["message_index"],
    })
    projects.switcher.switching.discard(binding.project_workspace_id)
    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("index,checkpoint,status", [(0, None, 422), (999, None, 422), (1, "missing", 404), (-1, None, 422)])
async def test_invalid_fork_targets(fork_setup, index, checkpoint, status):
    source_id, _, _ = await source_thread(fork_setup)
    target = await fork_target(source_id)
    response = await fork_setup.client.post(f"/dashboard-api/chat-history/{source_id}/fork", json={
        "source_checkpoint_id": checkpoint or target["source_checkpoint_id"], "message_index": index,
    })
    assert response.status_code == status
    assert len(await get_conversation_thread_repository().list()) == 1


@pytest.mark.asyncio
async def test_busy_source_is_rejected(fork_setup):
    source_id, _, _ = await source_thread(fork_setup)
    target = await fork_target(source_id)
    with get_active_session_registry().reserve_thread_mutation(source_id):
        response = await fork_setup.client.post(f"/dashboard-api/chat-history/{source_id}/fork", json={
            "source_checkpoint_id": target["source_checkpoint_id"], "message_index": target["message_index"],
        })
    assert response.status_code == 409
    from agent.modules.agent_runtime.active_sessions import ActiveSession

    registry = get_active_session_registry()
    session_id = registry.acquire(ActiveSession(
        thread_id=source_id, platform="api", user_id="dashboard", channel_id="", agent_name="default",
    ))
    try:
        response = await fork_setup.client.post(f"/dashboard-api/chat-history/{source_id}/fork", json={
            "source_checkpoint_id": target["source_checkpoint_id"], "message_index": target["message_index"],
        })
        assert response.status_code == 409
    finally:
        registry.release(session_id)


@pytest.mark.asyncio
async def test_temp_fork_rebinds_paths_and_retains_active_skill_snapshots(fork_setup, tmp_path, monkeypatch):
    from agent.modules.skills.context import activation_key
    from agent.modules.skills.snapshots import SnapshotStore
    from agent.modules.skills.sources import workspace_key
    from agent.modules.tools import conversation_key

    source_id, binding, config = await source_thread(fork_setup)
    snapshots = SnapshotStore(tmp_path / "skill-snapshots")
    version = snapshots.retain(b"pinned-package", conversation_key(source_id))
    key = activation_key("default", binding.execution, "example")
    monkeypatch.setattr("agent.modules.skills.get_skill_packages", lambda: SimpleNamespace(snapshots=snapshots))
    old_path = str(thread_storage.workspace_storage_root(binding.scope) / "uploads/input.txt")
    thread_storage.ingest_attachment_file("input.txt", b"file", binding.scope, binding.execution.locator)
    await fork_setup.graph.ainvoke({
        "messages": [HumanMessage(content=f"Read {old_path} /dashboard-api/generated-images/x.png?thread_id={source_id}")],
        "active_skills": {key: {
            "id": "c" * 32, "agent_name": "default", "name": "example", "version": version,
            "workspace_key": workspace_key(binding.execution), "workspace_root": binding.execution.locator,
        }},
    }, config)
    selected = await fork_target(source_id)
    fork = await forking.fork_conversation_thread(source_id, source_checkpoint_id=selected["source_checkpoint_id"], message_index=selected["message_index"])
    new_binding = forking.WorkspaceBinding.model_validate(fork["workspace"])
    state = await fork_setup.graph.aget_state({"configurable": {"thread_id": fork["thread_id"]}})
    new_key = activation_key("default", new_binding.execution, "example")
    assert list(state.values["active_skills"]) == [new_key]
    entry = state.values["active_skills"][new_key]
    assert entry["workspace_key"] == workspace_key(new_binding.execution)
    assert entry["workspace_root"] == new_binding.execution.locator
    text = state.values["messages"][-2].content
    assert old_path not in text
    assert f"thread_id={fork['thread_id']}" in text
    archive = snapshots.root / f"{version}.zip"
    old_time = time.time() - 8 * 86400
    os.utime(archive, (old_time, old_time))
    snapshots.release(conversation_key(source_id))
    assert snapshots.read(version) == b"pinned-package"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "project"])
async def test_temp_fork_preserves_source_mode_skills_without_snapshot_archives(fork_setup, tmp_path, monkeypatch, scope):
    from agent.modules.agents import AgentConfig
    from agent.modules.skills import get_repository_skill_dir
    from agent.modules.skills.context import activation_key
    from agent.modules.skills.packages import SkillPackages
    from agent.modules.skills.repository import FilesystemSkillRepository

    setup = fork_setup
    source_id, binding, config = await source_thread(setup)
    setup.container.agent_repository._cache["default"] = AgentConfig(
        name="default", graph_type="react_agent", provider="default", tools=["skill", "read", "bash"],
    )
    global_root = tmp_path / "skills"
    global_root.mkdir()
    source = (global_root if scope == "global" else Path(binding.execution.locator) / get_repository_skill_dir()) / "example"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: example\ndescription: Source execution check.\n---\nRead asset.txt.\n", encoding="utf-8")
    (source / "asset.txt").write_text("Original resource", encoding="utf-8")
    packages = SkillPackages(FilesystemSkillRepository(global_root))
    original_get = setup.container.config_service.get
    monkeypatch.setattr(setup.container.config_service, "get", lambda key, default=None: (
        "source" if key == "skills.local_execution_mode" else original_get(key, default)
    ))
    monkeypatch.setattr("agent.modules.skills.get_skill_packages", lambda: packages)
    loaded = await packages.activate("example", workspace=binding.execution, thread_id=source_id, agent_name="default")
    assert loaded.execution_mode == "source"
    assert not packages.snapshots.root.exists()
    await setup.graph.ainvoke({
        "messages": [HumanMessage(content="Use the example skill")],
        "active_skills": {activation_key("default", binding.execution, "example"): asdict(loaded)},
    }, config)
    selected = await fork_target(source_id)

    response = await setup.client.post(f"/dashboard-api/chat-history/{source_id}/fork", json={
        "source_checkpoint_id": selected["source_checkpoint_id"], "message_index": selected["message_index"],
    })
    assert response.status_code == 201, response.text
    fork = response.json()
    new_binding = forking.WorkspaceBinding.model_validate(fork["workspace"])
    state = await setup.graph.aget_state({"configurable": {"thread_id": fork["thread_id"]}})
    entry = state.values["active_skills"][activation_key("default", new_binding.execution, "example")]
    assert entry["execution_mode"] == "source" and entry["version"] == loaded.version
    restored = await packages.restore(entry, workspace=new_binding.execution, thread_id=fork["thread_id"])
    assert restored["execution_mode"] == "source"
    assert (Path(restored["skill_root"]) / "asset.txt").read_text() == "Original resource"
    assert not packages.snapshots.root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["daytona", "modal"])
async def test_cloud_temp_fork_captures_current_files(fork_setup, tmp_path, monkeypatch, backend):
    from agent.modules.projects import get_project_workspace_service

    remote = WorkspaceRef(backend=backend, locator="source-sandbox", metadata={"temp": True, "root": "/workspace"})
    source_id, binding, _ = await source_thread(fork_setup, workspace=bind_workspace_ref(remote))
    target = await fork_target(source_id)
    remote_files = tmp_path / "remote-files"
    remote_files.mkdir()
    (remote_files / "file.txt").write_text("Current cloud files")
    images = remote_files / ".k41-agent/generated-images"
    images.mkdir(parents=True)
    (images / "cloud.png").write_bytes(b"cloud-image")
    projects = get_project_workspace_service()
    capture = projects.transfer.capture
    captured = []

    async def capture_remote(ref, store, key):
        captured.append(ref)
        return await capture(WorkspaceRef(locator=str(remote_files)), store, key)

    monkeypatch.setattr(projects.transfer, "capture", capture_remote)
    fork = await forking.fork_conversation_thread(source_id, source_checkpoint_id=target["source_checkpoint_id"], message_index=target["message_index"])
    assert len(captured) == 1
    assert (captured[0].backend, captured[0].locator, captured[0].metadata) == (remote.backend, remote.locator, remote.metadata)
    assert fork["workspace"]["execution"]["backend"] == "local"
    assert fork["workspace"]["execution"]["metadata"]["temp"] is True
    assert (Path(fork["workspace"]["execution"]["locator"]) / "file.txt").read_text() == "Current cloud files"
    assert (await resolve_storage_file("cloud.png", thread_id=fork["thread_id"])).read_bytes() == b"cloud-image"
    stored = await get_thread_workspace_repository().get(source_id)
    assert stored["workspace"]["execution"]["backend"] == backend


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["copy", "checkpoint", "metadata"])
async def test_failure_removes_partial_fork_without_touching_source(fork_setup, monkeypatch, stage):
    source_id, binding, config = await source_thread(fork_setup)
    before = await fork_setup.graph.aget_state(config)
    target = await fork_target(source_id)
    if stage == "copy":
        def fail_copy(source, destination):
            (Path(destination.locator) / "partial.txt").write_text("partial")
            raise OSError("Copy failed")
        monkeypatch.setattr(forking, "_copy_temp_workspace", fail_copy)
    elif stage == "checkpoint":
        original = fork_setup.graph.aupdate_state
        async def fail_checkpoint(config, values, **kwargs):
            if kwargs.get("as_node") == END:
                raise OSError("Checkpoint failed")
            return await original(config, values, **kwargs)
        monkeypatch.setattr(fork_setup.graph, "aupdate_state", fail_checkpoint)
    else:
        async def fail_metadata(**kwargs):
            raise OSError("Metadata failed")
        monkeypatch.setattr(get_conversation_thread_repository(), "upsert", fail_metadata)
    with pytest.raises(OSError):
        await forking.fork_conversation_thread(source_id, source_checkpoint_id=target["source_checkpoint_id"], message_index=target["message_index"])
    assert (await fork_setup.graph.aget_state(config)).values == before.values
    assert [row["thread_id"] for row in await get_conversation_thread_repository().list()] == [source_id]
    assert list(Path(binding.execution.locator).parent.iterdir()) == [Path(binding.execution.locator)]
    checkpoint_threads = {row.config["configurable"]["thread_id"] async for row in fork_setup.container._checkpointer.alist(None)}
    assert checkpoint_threads == {source_id}


def test_only_final_answers_are_forkable():
    messages = [
        HumanMessage(content="First"), AIMessage(content="Done"),
        HumanMessage(content="Second"), AIMessage(content="Thinking"),
        AIMessage(content="Calling", tool_calls=[{"id": "call", "name": "read", "args": {}}]),
        ToolMessage(content="Result", tool_call_id="call"),
    ]
    assert forkable_message_indices(messages) == {1}
    messages.append(AIMessage(content="Second answer"))
    assert forkable_message_indices(messages) == {1, 6}
    messages.append(AIMessage(content="Summary", additional_kwargs={"is_compact_summary": True}))
    assert forkable_message_indices(messages) == {1}


@pytest.mark.asyncio
async def test_checkpoint_from_another_thread_and_intermediate_answers_are_rejected(fork_setup, monkeypatch):
    source_id, _, _ = await source_thread(fork_setup)
    other_id, _, _ = await source_thread(fork_setup, thread_id="d" * 32)
    foreign = await fork_target(other_id)
    with pytest.raises(forking.CheckpointNotFoundError):
        await forking.fork_conversation_thread(source_id, source_checkpoint_id=foreign["source_checkpoint_id"], message_index=foreign["message_index"])

    builder = StateGraph(BaseState)
    builder.add_node("research", lambda state: {"messages": [AIMessage(content="Preliminary findings")]})
    builder.add_node("summarize", lambda state: {"messages": [AIMessage(content="Final research answer")]})
    builder.add_edge(START, "research")
    builder.add_edge("research", "summarize")
    builder.add_edge("summarize", END)
    graph = builder.compile(checkpointer=fork_setup.container._checkpointer)
    monkeypatch.setattr(forking, "get_workflow_graph", lambda name: graph)
    config = {"configurable": {"thread_id": source_id}}
    await graph.ainvoke({"messages": [HumanMessage(content="Research something")]}, config)
    history = [state async for state in graph.aget_state_history(config)]
    intermediate = next(state for state in history if state.next == ("summarize",))
    with pytest.raises(ValueError, match="not completed"):
        await forking.fork_conversation_thread(
            source_id, source_checkpoint_id=intermediate.config["configurable"]["checkpoint_id"],
            message_index=len(intermediate.values["messages"]) - 1,
        )
