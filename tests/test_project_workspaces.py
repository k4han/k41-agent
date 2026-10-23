"""Project identity, publication, recovery, and migration integration tests."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.modules.projects import service as catalog
from agent.modules.projects.models import Project, ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.storage import LocalProjectStorage
from agent.modules.projects.transfer import WorkspaceSnapshotTransfer
from agent.modules.projects.workspaces import ProjectWorkspaceError, ProjectWorkspaceService
from agent.modules.workspaces.refs import WorkspaceRef, bind_workspace_ref
from agent.modules.workspaces.models import ThreadWorkspace
from agent.modules.agent_runtime.models import BackgroundTaskRecord
from agent.modules.conversations.models import ConversationThread
from agent.modules.workspaces.backends import WorkspaceUnavailableError


@pytest_asyncio.fixture
async def project_service(tmp_path, isolated_container, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "projects.db").as_posix())
    async with engine.begin() as connection:
        for model in (Project, ProjectWorkspace, WorkspaceRuntime, ThreadWorkspace, BackgroundTaskRecord, ConversationThread):
            await connection.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async def session():
        return maker()
    monkeypatch.setattr(catalog, "get_async_session", session)
    service = ProjectWorkspaceService(isolated_container, storage=LocalProjectStorage(tmp_path / "storage"))
    isolated_container._project_workspace_service = service
    isolated_container._persistence_ready = True
    root = tmp_path / "source"
    root.mkdir()
    (root / "file.txt").write_text("original")
    project = await catalog.add_project(WorkspaceRef(locator=str(root)))
    yield service, project, root, maker
    await service.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_preserves_previous_head_and_blocks_writes_on_failure(project_service, monkeypatch):
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    first = await service.checkpoint(project["id"])
    (root / "file.txt").write_text("changed")
    original = service.storage.put_blob
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(service.storage, "put_blob", fail)
    with pytest.raises(ProjectWorkspaceError, match="disk full"):
        await service.checkpoint(project["id"])
    _, row, _ = await service.rows(project["id"])
    assert row.head_revision == first["revision"]
    assert row.persistence_status == "error"
    with pytest.raises(ProjectWorkspaceError, match="unsaved"):
        async with service.mutation(project["id"], binding.project_workspace_id):
            pytest.fail("Writes must be blocked")
    monkeypatch.setattr(service.storage, "put_blob", original)
    second = await service.checkpoint(project["id"])
    assert second["revision"] != first["revision"]
    _, row, _ = await service.rows(project["id"])
    assert row.previous_revision == first["revision"] and row.persistence_status == "clean"


@pytest.mark.asyncio
async def test_concurrent_recovery_keeps_identity_and_restores_before_ready(project_service, monkeypatch, tmp_path):
    service, project, root, maker = project_service
    await service.ensure(project["id"])
    binding = await service.binding(project["id"])
    old = WorkspaceRef(backend="modal", locator="expired", metadata={"root": "/workspace"})
    session = await catalog.get_async_session()
    async with session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = old.model_dump_json()
        await session.commit()
    creates = []
    async def attach(backend, locator, **kwargs):
        if locator == "expired":
            raise WorkspaceUnavailableError("expired")
        return WorkspaceRef(backend="modal", locator=locator, metadata={"root": "/workspace"})
    async def create(backend, **kwargs):
        creates.append(backend)
        return WorkspaceRef(backend="modal", locator="replacement", metadata={"root": "/workspace"})
    import agent.modules.workspaces as runtime_service
    monkeypatch.setattr(runtime_service, "attach_workspace_backend", attach)
    monkeypatch.setattr(runtime_service, "create_workspace_backend", create)
    restored = tmp_path / "restored"
    async def restore(ref, store, key, revision):
        _, _, runtime = await service.rows(project["id"])
        assert runtime.status == "restoring"
        service.transfer.materialize(store, key, revision, restored)
    monkeypatch.setattr(service.transfer, "restore", restore)
    results = await asyncio.gather(service.ensure(project["id"]), service.ensure(project["id"]))
    assert creates == ["modal"]
    assert {result.project_id for result in results} == {project["id"]}
    assert {result.scope.key for result in results} == {project["key"]}
    assert {result.execution.locator for result in results} == {"replacement"}
    assert (restored / "file.txt").read_text() == "original"
    with pytest.raises(ProjectWorkspaceError, match="obsolete"):
        await service.checkpoint(project["id"], generation=binding.runtime_generation)
    service.active.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["read", "write"])
async def test_batch_preparation_recovers_runtime_before_authorization(project_service, monkeypatch, tmp_path, name):
    from agent.modules.tools.coding.adapter import SCHEMAS
    from agent.modules.tools.coding.models import CodingInvocation, InvocationContext, ToolDefinition, ToolResult
    from agent.modules.tools.coding.service import CodingService
    import agent.modules.workspaces as runtimes

    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    expired = WorkspaceRef(backend="modal", locator="expired", metadata={"root": "/workspace"})
    replacement = expired.model_copy(update={"locator": "replacement"})
    thread_id = "api_recovery"
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        session.add(ThreadWorkspace(thread_id=thread_id, project_id=project["id"],
                                    project_workspace_id=binding.project_workspace_id,
                                    execution_backend=expired.backend, execution_locator=expired.locator))
        await session.commit()
    service.container._persistence_ready = True

    async def attach(backend, locator, **kwargs):
        if locator == expired.locator:
            raise WorkspaceUnavailableError("expired")
        return replacement

    restored = tmp_path / "restored"
    async def restore(ref, store, key, revision):
        _, _, runtime = await service.rows(project["id"])
        assert runtime.status == "restoring"
        service.transfer.materialize(store, key, revision, restored)

    create = AsyncMock(return_value=replacement)
    monkeypatch.setattr(runtimes, "attach_workspace_backend", attach)
    monkeypatch.setattr(runtimes, "create_workspace_backend", create)
    monkeypatch.setattr(service.transfer, "restore", restore)
    coding = CodingService(tmp_path / "coding")
    prepared = []
    async def prepare(operation, values, context):
        assert context.locator == "replacement"
        assert context.workspace == "/workspace"
        assert context.project_workspace_id == binding.project_workspace_id
        assert context.runtime_generation == binding.runtime_generation + 1
        assert (restored / "file.txt").read_text() == "original"
        prepared.append(context)

    monkeypatch.setattr(coding.remote, "prepare", prepare)
    execute = AsyncMock()
    definition = ToolDefinition(name, name, SCHEMAS[name], execute)
    args = {"path": "file.txt", **({"content": "updated"} if name == "write" else {})}
    context = InvocationContext("test", "/workspace", thread_id, tool_call_id="call", backend="modal", locator="expired")
    invocations = [CodingInvocation(definition, args, context)]
    try:
        assert await coding.prepare_batch(invocations) is None
        create.assert_awaited_once()
        execute.assert_not_awaited()
        assert len(prepared) == 1
        if name == "write":
            fingerprint = coding.fingerprint(name, SCHEMAS[name].model_validate(args).model_dump())
            coding.storage.complete(prepared[0], fingerprint, ToolResult(content="already executed"))
            assert await coding.prepare_batch(invocations) is None
            assert len(prepared) == 1
            execute.assert_not_awaited()
    finally:
        service.active.clear()
        await coding.processes.close()
        await coding.storage.close()


@pytest.mark.asyncio
async def test_migration_preserves_divergent_sources_until_explicit_selection(project_service, tmp_path):
    service, project, root, maker = project_service
    other = tmp_path / "other"
    other.mkdir()
    (other / "file.txt").write_text("different")
    async with maker() as session:
        row = await session.get(Project, project["id"])
        row.migration_status = "pending"
        shared = (await session.execute(select(ProjectWorkspace))).scalar_one()
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        await session.delete(runtime)
        await session.delete(shared)
        for index, path in enumerate((root, other)):
            session.add(ThreadWorkspace(thread_id=f"thread-{index}", scope_key=project["key"], scope_kind="local",
                                        execution_backend="local", execution_locator=str(path)))
        await session.commit()
    await service.migrate(project["id"])
    description = await service.describe(project["id"])
    assert description["migration_status"] == "needs_selection"
    assert len(description["workspaces"]) == 2
    assert all(item["persistence"]["revision"] for item in description["workspaces"])
    source_id = description["workspaces"][1]["id"]
    selected = await service.select_migration_source(project["id"], source_id)
    assert selected.project_id == project["id"]
    assert (await service.describe(project["id"]))["migration_status"] == "ready"
    async with maker() as session:
        mappings = list((await session.execute(select(ThreadWorkspace))).scalars())
        assert {mapping.project_workspace_id for mapping in mappings} == {source_id}
    assert (root / "file.txt").read_text() == "original"
    assert (other / "file.txt").read_text() == "different"


@pytest.mark.asyncio
async def test_failed_save_never_reexecutes_completed_tool(project_service, monkeypatch, tmp_path):
    from agent.modules.tools.coding.service import CodingService
    from agent.modules.tools.coding.models import InvocationContext, PermissionRule, ToolDefinition
    from agent.modules.tools.coding.schemas import WriteInput
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    coding = CodingService(tmp_path / "coding")
    calls = []
    async def execute(params, context, runtime):
        calls.append(context.tool_call_id)
        return await runtime.execute("write", params.model_dump(), context)
    definition = ToolDefinition("write", "Write a project file", WriteInput, execute)
    context = InvocationContext(agent_name="test", workspace=str(root), thread_id="thread", message_id="message",
        tool_call_id="once", permission_rules=(PermissionRule(action="*", resource="*", effect="allow"),),
        project_id=project["id"], project_workspace_id=binding.project_workspace_id)
    put = service.storage.put_manifest
    def fail(*args):
        raise OSError("storage unavailable")
    monkeypatch.setattr(service.storage, "put_manifest", fail)
    try:
        first = await coding.invoke(definition, {"path": "new.txt", "content": "updated"}, context)
        assert first.status == "success"
        assert first.data["persistence"]["saved"] is False
        assert "not been saved" in first.content
        replay = await coding.invoke(definition, {"path": "new.txt", "content": "updated"}, context)
        assert replay.status == "success" and calls == ["once"]
        blocked = await coding.invoke(definition, {"path": "other.txt", "content": "blocked"}, replace(context, tool_call_id="twice"))
        assert blocked.status == "error" and blocked.error.code == "writes_blocked"
        assert not (root / "other.txt").exists()
        monkeypatch.setattr(service.storage, "put_manifest", put)
        await service.checkpoint(project["id"])
        replay = await coding.invoke(definition, {"path": "new.txt", "content": "updated"}, context)
        assert calls == ["once"]
    finally:
        await coding.processes.close()


@pytest.mark.asyncio
async def test_task_workspace_is_independent_and_receipts_survive_generation(project_service):
    from agent.modules.projects.workspaces import register_workspace
    from agent.modules.tools.coding.contracts import InvocationContext
    from agent.modules.tools.coding.storage import OutputStore
    service, project, root, maker = project_service
    shared = await service.ensure(project["id"])
    task_root = root.parent / "task"
    task_root.mkdir()
    (task_root / "file.txt").write_text("task changes")
    async with maker() as session:
        record = await session.get(Project, project["id"])
        task = await register_workspace(session, record, WorkspaceRef(locator=str(task_root)), kind="github_task")
        await session.commit()
    await service.checkpoint(project["id"], task.project_workspace_id)
    assert await service.read_saved(project["id"], "file.txt") != await service.read_saved(project["id"], "file.txt", task.project_workspace_id)
    assert (await service.binding(project["id"])).project_workspace_id == shared.project_workspace_id
    store = OutputStore(root.parent / "receipts")
    before = InvocationContext("agent", "/workspace", "thread", message_id="m", tool_call_id="t", backend="modal", locator="old",
                               project_id=project["id"], project_workspace_id=shared.project_workspace_id, runtime_generation=1)
    after = replace(before, locator="new", runtime_generation=2)
    assert store.journal_path(before) == store.journal_path(after)
    assert before.owner != after.owner


@pytest.mark.asyncio
async def test_project_reference_conflicts_and_retired_runtime_aliases(project_service):
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    with pytest.raises(ProjectWorkspaceError, match="project_id"):
        await service.find_binding(binding, project_id=project["id"] + 1)
    with pytest.raises(ProjectWorkspaceError, match="runtime changed"):
        await service.find_binding(binding.model_copy(update={"runtime_generation": 99}))
    project_row, workspace, runtime = await service.rows(project["id"])
    for generation in (2, 3):
        target = root.parent / f"generation-{generation}"
        target.mkdir()
        (target / "file.txt").write_text("original")
        await service._publish_runtime(project_row, workspace, WorkspaceRef(locator=str(target)), generation, "ready")
    service.container._persistence_ready = True
    middle = WorkspaceRef(locator=str(root.parent / "generation-2"))
    current = await service.find_binding(middle)
    assert current.project_id == project["id"] and current.runtime_generation == 3
    assert current.scope.key == project["key"]


@pytest.mark.asyncio
async def test_explicit_workspace_overrides_thread_default_and_rejects_conflicting_locator(project_service):
    from agent.delivery.http.dashboard.routes.helpers.workspace import workspace_ref_from_request
    from agent.delivery.http.dashboard.routes.workspace import resolve_dashboard_workspace, WorkspaceResolveBody
    from fastapi import HTTPException
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    service.container._persistence_ready = True
    async with maker() as session:
        session.add(ThreadWorkspace(thread_id="api_test", project_id=project["id"],
                                    project_workspace_id=binding.project_workspace_id))
        await session.commit()
    other_root = root.with_name("other-project")
    other_root.mkdir()
    other = await catalog.add_project(WorkspaceRef(locator=str(other_root)))
    requested = await service.find_binding(WorkspaceRef(locator=str(other_root)), thread_id="api_test")
    assert requested.project_id == other["id"]
    with pytest.raises(ProjectWorkspaceError, match="conflicts"):
        await workspace_ref_from_request(project_id=project["id"], backend="local", locator=str(other_root))
    with pytest.raises(HTTPException) as failure:
        await resolve_dashboard_workspace(None, WorkspaceResolveBody(
            project_id=project["id"], backend="local", locator=str(other_root),
        ))
    assert failure.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True])
async def test_thread_workspace_switch_updates_project_and_coding_context(project_service, monkeypatch, registered):
    from agent.modules.tools.coding.contracts import InvocationContext
    from agent.modules.workspaces import repository as mappings
    service, project, root, maker = project_service
    monkeypatch.setattr(mappings, "get_async_session", catalog.get_async_session)
    service.container._persistence_ready = True
    repository = mappings.ThreadWorkspaceRepository()
    await repository.upsert(thread_id="api_switch", workspace=WorkspaceRef(locator=str(root)))
    other_root = root.with_name("other-project")
    other_root.mkdir()
    other_ref = WorkspaceRef(locator=str(other_root))
    other = await catalog.add_project(other_ref) if registered else None

    stored = await repository.upsert(thread_id="api_switch", workspace=other_ref)
    resolved = await service.resolve_thread("api_switch")
    context = await service.resolve_context(InvocationContext("agent", str(other_root), "api_switch"))

    assert resolved.project_id != project["id"]
    if other:
        assert resolved.project_id == other["id"]
    assert stored["workspace"]["project_id"] == resolved.project_id == context.project_id
    assert stored["workspace"]["execution"]["locator"] == resolved.execution.locator == context.workspace == str(other_root)
    assert (await service.binding(project["id"])).execution.locator == str(root)


@pytest.mark.asyncio
async def test_thread_workspace_switch_to_retired_reference_keeps_project_identity(project_service, monkeypatch):
    from agent.modules.workspaces import repository as mappings
    service, project, root, maker = project_service
    monkeypatch.setattr(mappings, "get_async_session", catalog.get_async_session)
    service.container._persistence_ready = True
    repository = mappings.ThreadWorkspaceRepository()
    await repository.upsert(thread_id="api_alias", workspace=WorkspaceRef(locator=str(root)))
    project_row, workspace, _ = await service.rows(project["id"])
    replacements = []
    for generation in (2, 3):
        target = root.with_name(f"runtime-{generation}")
        target.mkdir()
        replacement = WorkspaceRef(locator=str(target))
        replacements.append(replacement)
        await service._publish_runtime(project_row, workspace, replacement, generation, "ready")

    await repository.upsert(thread_id="api_alias", workspace=replacements[0])
    resolved = await service.resolve_thread("api_alias")

    assert resolved.project_id == project["id"]
    assert resolved.project_workspace_id == workspace.id
    assert resolved.execution.locator == replacements[-1].locator
    assert resolved.runtime_generation == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["dirty", "error"])
async def test_github_prune_preserves_unsaved_worktree_until_checkpoint(project_service, status):
    from agent.modules.github.service import GitHubAutomationService
    from agent.modules.github.workspace import GitHubWorkspaceManager
    from agent.modules.projects.workspaces import register_workspace
    service, project, root, maker = project_service
    service.container._persistence_ready = True
    github_root = root.with_name("github")
    task_root = github_root / "owner/repo.worktrees/task"
    task_root.mkdir(parents=True)
    (task_root / "file.txt").write_text("saved")
    async with maker() as session:
        record = await session.get(Project, project["id"])
        binding = await register_workspace(session, record, WorkspaceRef(locator=str(task_root)), kind="github_task")
        await session.commit()
    await service.checkpoint(project["id"], binding.project_workspace_id)
    async with service.mutation(project["id"], binding.project_workspace_id):
        (task_root / "file.txt").write_text("unsaved changes")
    if status == "error":
        await service._set_error(binding.project_workspace_id, OSError("storage unavailable"))
    pointer = task_root / ".git"
    pointer.write_text("gitdir: /missing/repo/.git/worktrees/task\n")
    github = GitHubAutomationService(client=SimpleNamespace(), store=SimpleNamespace(),
                                     workspace_manager=GitHubWorkspaceManager(root=github_root))

    assert await github.prune_orphaned_worktrees() == 0
    assert (task_root / "file.txt").read_text() == "unsaved changes"
    saved, _ = await service.read_saved(project["id"], "file.txt", binding.project_workspace_id)
    assert saved == b"saved"

    pointer.unlink()
    await service.checkpoint(project["id"], binding.project_workspace_id)
    pointer.write_text("gitdir: /missing/repo/.git/worktrees/task\n")
    assert await github.prune_orphaned_worktrees() == 1
    assert not task_root.exists()
    saved, _ = await service.read_saved(project["id"], "file.txt", binding.project_workspace_id)
    assert saved == b"unsaved changes"
    await service.release_task_runtime(binding)


@pytest.mark.asyncio
async def test_instruction_read_uses_saved_copy_when_local_execution_disappears(project_service):
    from agent.modules.projects.resources import get_project_instructions
    from agent.modules.skills import WorkspacePackageIO
    service, project, root, maker = project_service
    (root / "AGENTS.md").write_bytes(b"# Durable instructions\n")
    skill = root / ".agents/skills/example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"---\nname: example\ndescription: Saved skill\n---\n# Example\n")
    binding = await service.ensure(project["id"])
    service.container._persistence_ready = True
    offline = root.with_name("offline")
    root.rename(offline)
    try:
        document = await get_project_instructions(project["id"])
        assert document["content"] == "# Durable instructions\n"
        assert document["saved_revision"] and document["last_saved_at"]
        result = await WorkspacePackageIO(binding.execution).operation("scan", ".agents/skills")
        assert len(result["skills"]) == 1
        assert result["saved_revision"] == document["saved_revision"]
        assert result["last_saved_at"] == document["last_saved_at"]
        assert not root.exists()
    finally:
        offline.rename(root)


@pytest.mark.asyncio
async def test_incomplete_runtime_can_never_publish_a_partial_checkpoint(project_service):
    service, project, root, maker = project_service
    await service.ensure(project["id"])
    _, before, _ = await service.rows(project["id"])
    (root / "file.txt").write_text("incomplete restored copy")
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.status = "restoring"
        await session.commit()
    with pytest.raises(ProjectWorkspaceError, match="Restore the workspace"):
        await service.checkpoint(project["id"])
    _, after, _ = await service.rows(project["id"])
    assert after.head_revision == before.head_revision
    service.active.clear()


@pytest.mark.asyncio
async def test_added_project_is_monitored_after_periodic_start(project_service, monkeypatch, tmp_path):
    service, project, root, maker = project_service
    initial = await service.binding(project["id"])
    service.active.clear()
    saved = asyncio.Queue()
    checkpoint = service.checkpoint

    async def record_checkpoint(project_id, workspace_id=None, **kwargs):
        result = await checkpoint(project_id, workspace_id, **kwargs)
        saved.put_nowait(workspace_id)
        return result

    monkeypatch.setattr(service, "checkpoint", record_checkpoint)
    service.start()

    async def wait_for_startup():
        while initial.project_workspace_id not in service.active:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_for_startup(), timeout=10)
    added_root = tmp_path / "added"
    added_root.mkdir()
    (added_root / "file.txt").write_bytes(b"added without a conversation")
    added = await catalog.add_project(WorkspaceRef(locator=str(added_root)))
    binding = await service.binding(added["id"])
    assert binding.project_workspace_id in service.active

    async def wait_for_checkpoint():
        while await saved.get() != binding.project_workspace_id:
            pass

    await asyncio.wait_for(wait_for_checkpoint(), timeout=10)
    assert (await service.read_saved(added["id"], "file.txt"))[0] == b"added without a conversation"
    (added_root / "file.txt").write_bytes(b"external edit before shutdown")
    await service.close()
    assert (await service.read_saved(added["id"], "file.txt"))[0] == b"external edit before shutdown"


@pytest.mark.asyncio
async def test_added_project_is_saved_on_shutdown_without_periodic_start(project_service):
    service, project, root, maker = project_service
    await service.close()
    assert (await service.read_saved(project["id"], "file.txt"))[0] == b"original"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["local", "modal"])
async def test_running_process_has_one_watcher_across_output_reads(project_service, monkeypatch, tmp_path, backend):
    from agent.modules.tools.coding.contracts import RuntimeResult
    from agent.modules.tools.coding.engine import CodingEngine
    from agent.modules.tools.coding.models import InvocationContext, ToolResult
    from agent.modules.tools.coding.service import CodingService

    service, project, root, maker = project_service
    binding = await service.binding(project["id"])
    coding = CodingService(tmp_path / "coding")
    context = InvocationContext("test", str(root), "thread", backend=backend, locator="sandbox",
                                project_id=project["id"], project_workspace_id=binding.project_workspace_id,
                                runtime_generation=binding.runtime_generation)
    finished = {process_id: asyncio.Event() for process_id in ("first", "second")}
    checkpoint = AsyncMock(return_value={"saved": True})
    monkeypatch.setattr(service, "checkpoint", checkpoint)
    monkeypatch.setattr(coding.processes, "get", lambda context, process_id: SimpleNamespace(finished=finished[process_id]))

    def result(name, values, context):
        result_type = RuntimeResult if backend == "local" else ToolResult
        return result_type(status="running", data={"process_id": values["process_id"]})

    monkeypatch.setattr(CodingEngine, "execute", AsyncMock(side_effect=result))
    monkeypatch.setattr(coding.remote, "execute", AsyncMock(side_effect=result))

    async def process_state(request):
        await finished[request["values"]["process_id"]].wait()
        return {"finished": True}

    monkeypatch.setattr(coding.remote, "client", AsyncMock(return_value=SimpleNamespace(call=process_state)))
    try:
        for name in ("bash", "read_process_output", "read_process_output", "write_process_input"):
            await coding.execute(name, {"process_id": "first"}, context)
        assert len(coding.project_watchers) == 1
        await coding.execute("bash", {"process_id": "second"}, context)
        assert len(coding.project_watchers) == 2
        assert service.running_processes == {(binding.project_workspace_id, process_id) for process_id in finished}
        for event in finished.values():
            event.set()
        await asyncio.wait_for(asyncio.gather(*coding.project_watchers), timeout=5)
        assert checkpoint.await_count == 2
        assert not service.running_processes
        assert not coding.project_watchers
    finally:
        for task in tuple(coding.project_watchers):
            task.cancel()
        await asyncio.gather(*coding.project_watchers, return_exceptions=True)
        await coding.processes.close()
        await coding.storage.close()


@pytest.mark.asyncio
async def test_restart_resumes_checkpoints_and_shutdown_saves_live_files(project_service):
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    service.active.clear()
    restarted = ProjectWorkspaceService(service.container, storage=service.storage)
    restarted.start()
    async def wait_for_resumed_runtime():
        while binding.project_workspace_id not in restarted.active:
            await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(wait_for_resumed_runtime(), timeout=10)
        (root / "file.txt").write_bytes(b"changed after restart")
    finally:
        await restarted.close()
    content, _ = await restarted.read_saved(project["id"], "file.txt")
    assert content == b"changed after restart"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_save", [False, True])
async def test_deleting_last_conversation_keeps_project_files_and_runtime(project_service, monkeypatch, fail_save):
    import agent.modules.workspaces.repository as mappings
    import agent.modules.workspaces.service as runtimes
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    async def session():
        return maker()
    monkeypatch.setattr(mappings, "get_async_session", session)
    monkeypatch.setattr(runtimes, "_temp_workspace_owner_is_gone", AsyncMock(return_value=False))
    stop = AsyncMock()
    monkeypatch.setattr(service.container, "_coding_service", SimpleNamespace(processes=SimpleNamespace(stop_thread=stop)))
    service.container._persistence_ready = True
    await runtimes.remember_thread_workspace_ref("api_last", binding)
    if fail_save:
        def unavailable(*args):
            raise OSError("storage unavailable")
        monkeypatch.setattr(service.storage, "put_manifest", unavailable)
    await runtimes.delete_thread_workspace("api_last")
    assert await service.resolve_thread("api_last") is None
    stop.assert_awaited_once_with("api_last")
    current = await catalog.get_project(project["id"])
    assert current["id"] == project["id"] and current["key"] == project["key"]
    assert current["runtime"]["status"] == "ready"
    assert current["persistence"]["revision"]
    assert current["persistence"]["status"] == ("error" if fail_save else "clean")
    assert (root / "file.txt").read_text() == "original"


@pytest.mark.asyncio
async def test_cancelled_collection_cannot_delete_a_subsequent_checkpoint(project_service, monkeypatch):
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    started, release = Event(), Event()
    collect = service.storage.collect
    def blocked_collection(*args):
        started.set()
        if not release.wait(10):
            raise TimeoutError("Collection was not released")
        collect(*args)
    monkeypatch.setattr(service.storage, "collect", blocked_collection)
    first = asyncio.create_task(service.checkpoint(project["id"]))
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        await asyncio.sleep(0)
        assert service.lock(binding.project_workspace_id).locked()
        second = asyncio.create_task(service.checkpoint(project["id"]))
        (root / "file.txt").write_bytes(b"newer checkpoint")
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
    _, saved, _ = await service.rows(project["id"])
    assert saved.persistence_status == "clean"
    assert second is not None
    await second
    content, _ = await service.read_saved(project["id"], "file.txt")
    assert content == b"newer checkpoint"


@pytest.mark.asyncio
async def test_explicit_workspace_id_survives_partial_project_payload(project_service):
    from agent.modules.projects.workspaces import register_workspace
    service, project, root, maker = project_service
    async with maker() as session:
        record = await session.get(Project, project["id"])
        task = await register_workspace(session, record, WorkspaceRef(locator=str(root)), kind="github_task")
        await session.commit()
    payload = task.model_dump(exclude={"project_workspace_id"})
    binding = await service.find_binding(payload, project_id=project["id"], workspace_id=task.project_workspace_id)
    assert binding.project_workspace_id == task.project_workspace_id


@pytest.mark.asyncio
@pytest.mark.parametrize("closing", [False, True])
async def test_project_resolution_cannot_access_unavailable_persistence(project_service, monkeypatch, closing):
    service, project, root, maker = project_service
    service.closing = closing
    service.container._persistence_ready = closing
    read = AsyncMock(side_effect=AssertionError("Database must not be accessed"))
    monkeypatch.setattr(service, "binding", read)
    with pytest.raises(ProjectWorkspaceError, match="temporarily unavailable"):
        await service.find_binding(project_id=project["id"])
    assert await service.find_binding(WorkspaceRef(locator=str(root))) is None
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweeper_skips_destroyed_and_incomplete_project_runtimes(project_service, monkeypatch):
    from datetime import timedelta
    from agent.shared.infrastructure.db.base import utcnow
    service, project, root, maker = project_service
    retire = AsyncMock()
    monkeypatch.setattr(service, "retire_matching", retire)
    for status in ("destroyed", "archived", "restoring"):
        async with maker() as session:
            runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
            runtime.execution_json = WorkspaceRef(backend="daytona", locator="retired").model_dump_json()
            runtime.status = status
            runtime.last_used_at = utcnow() - timedelta(days=1)
            await session.commit()
        counts = await service.sweep_idle_daytona(now=utcnow(), stop_after=timedelta(minutes=5), archive_after=timedelta(hours=1))
        assert counts["checked"] == counts["skipped"] == 1
        assert counts["errors"] == []
    retire.assert_not_awaited()


@pytest.mark.asyncio
async def test_periodic_checkpoint_keeps_clean_timestamp_and_saves_external_changes(project_service):
    service, project, root, maker = project_service
    await service.ensure(project["id"])
    _, before, _ = await service.rows(project["id"])
    await service.checkpoint(project["id"], skip_unchanged=True)
    _, unchanged, _ = await service.rows(project["id"])
    assert unchanged.last_saved_at == before.last_saved_at
    (root / "file.txt").write_bytes(b"external changes")
    await service.checkpoint(project["id"], skip_unchanged=True)
    _, changed, _ = await service.rows(project["id"])
    assert changed.head_revision != before.head_revision
    assert (await service.read_saved(project["id"], "file.txt"))[0] == b"external changes"


@pytest.mark.asyncio
async def test_managed_commands_report_save_failure_without_reexecution(project_service, monkeypatch):
    from agent.modules.projects.backend import ManagedWorkspaceBackend
    from agent.modules.workspaces import CommandResult
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    execute = AsyncMock(return_value=CommandResult("completed", 0))
    backend = ManagedWorkspaceBackend(SimpleNamespace(execute=execute), binding, service)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(service.storage, "put_manifest", fail)
    result = await backend.execute("command")
    assert result.exit_code == 0 and result.persistence["saved"] is False
    assert "not been saved" in result.output
    execute.assert_awaited_once_with("command")
    with pytest.raises(ProjectWorkspaceError, match="unsaved"):
        await backend.execute("another command")
    assert execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["resolve", "ensure", "submit"])
@pytest.mark.parametrize("code,status", [(None, 404), ("workspace_conflict", 409), ("migration_required", 409),
                                        ("writes_blocked", 409), ("missing_data", 410)])
async def test_task_submission_maps_project_errors(project_service, monkeypatch, phase, code, status):
    from fastapi import HTTPException
    import agent.modules.projects as projects
    from agent.delivery.http.dashboard.routes import tasks
    service, project, root, maker = project_service
    error = KeyError("Project not found.") if code is None else ProjectWorkspaceError("Project unavailable", code)
    binding = await service.binding(project["id"])
    find = AsyncMock(return_value=binding)
    ensure = AsyncMock(return_value=binding)
    if phase != "submit":
        (find if phase == "resolve" else ensure).side_effect = error
    monkeypatch.setattr(projects, "get_project_workspace_service", lambda: SimpleNamespace(find_binding=find, ensure=ensure))
    submit = AsyncMock(side_effect=error if phase == "submit" else None)
    monkeypatch.setattr(tasks, "get_background_task_manager", lambda: SimpleNamespace(submit=submit))
    with pytest.raises(HTTPException) as failure:
        await tasks.submit_background_task(tasks.SubmitTaskBody(request="Work", workspace=binding.execution if phase == "submit" else binding))
    assert failure.value.status_code == status
    if phase == "submit":
        submit.assert_awaited_once()
        find.assert_not_awaited()
    else:
        submit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 1])
async def test_remote_publish_uses_one_managed_checkpoint(project_service, monkeypatch, exit_code):
    from agent.modules.github.workspace_helpers import remote_publish_branch
    from agent.modules.projects.backend import ManagedWorkspaceBackend
    import agent.modules.workspaces as workspaces
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    ref = binding.execution.model_copy(update={"metadata": {"repository_full_name": "team/repo"}})
    execute = AsyncMock(return_value=workspaces.CommandResult("result", exit_code))
    backend = ManagedWorkspaceBackend(SimpleNamespace(execute=execute), binding, service)
    monkeypatch.setattr(workspaces, "get_workspace_command_executor", AsyncMock(return_value=backend))
    checkpoint = AsyncMock(wraps=service._checkpoint)
    monkeypatch.setattr(service, "_checkpoint", checkpoint)
    if exit_code:
        with pytest.raises(RuntimeError, match="commit/push failed"):
            await remote_publish_branch(ref, "Fix user's issue", "task-branch", "test-token")
    else:
        await remote_publish_branch(ref, "Fix user's issue", "task-branch", "test-token")
    assert execute.await_count == checkpoint.await_count == 1
    assert (await service.describe(project["id"]))["persistence"]["status"] == "clean"


@pytest.mark.asyncio
async def test_migration_preserves_missing_local_source_without_creating_empty_workspace(project_service):
    service, project, root, maker = project_service
    root.rename(root.with_name("unavailable"))
    async with maker() as session:
        record = await session.get(Project, project["id"])
        record.migration_status = "pending"
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        workspace = (await session.execute(select(ProjectWorkspace))).scalar_one()
        await session.delete(runtime)
        await session.delete(workspace)
        await session.commit()
    await service.migrate(project["id"])
    description = await service.describe(project["id"])
    assert description["migration_status"] == "missing_data"
    assert description["workspaces"][0]["source"]["locator"] == str(root)
    assert description["workspaces"][0]["persistence"]["status"] == "error"
    with pytest.raises(ProjectWorkspaceError, match="Choose the main workspace"):
        await service.ensure(project["id"])
    assert not root.exists()
