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
from agent.modules.workspaces.host_mirror import workspace_runtime_status as probe_workspace_runtime_status


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
    original_bool = service.container.config_service.get_bool
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False:
                        True if key == "workspace.modal.enabled" else original_bool(key, default))
    with pytest.raises(ProjectWorkspaceError) as blocked:
        await service.ensure(project["id"])
    assert blocked.value.code == "provision_confirm_required"
    _, _, runtime = await service.rows(project["id"])
    generation = runtime.generation
    results = await asyncio.gather(
        service.confirm_provision(project["id"], "restore", generation),
        service.confirm_provision(project["id"], "restore", generation),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert creates == ["modal"]
    assert len(successes) == 1
    assert {result.project_id for result in successes} == {project["id"]}
    assert {result.scope.key for result in successes} == {project["key"]}
    assert {result.execution.locator for result in successes} == {"replacement"}
    assert (restored / "file.txt").read_text() == "original"
    with pytest.raises(ProjectWorkspaceError, match="obsolete"):
        await service.checkpoint(project["id"], generation=binding.runtime_generation)
    service.active.clear()


@pytest.mark.asyncio
async def test_lost_sandbox_records_decision_and_recovers_locally(project_service, monkeypatch, tmp_path):
    service, project, root, maker = project_service
    await service.ensure(project["id"])
    binding = await service.binding(project["id"])
    expired = WorkspaceRef(backend="daytona", locator="expired", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        await session.commit()
    async def attach(backend, locator, **kwargs):
        raise WorkspaceUnavailableError("sandbox is destroyed")
    async def create(backend, **kwargs):
        raise AssertionError("No cloud runtime may be created without the user's confirmation.")
    import agent.modules.workspaces as runtime_service
    monkeypatch.setattr(runtime_service, "attach_workspace_backend", attach)
    monkeypatch.setattr(runtime_service, "create_workspace_backend", create)
    with pytest.raises(ProjectWorkspaceError) as blocked:
        await service.ensure(project["id"])
    assert blocked.value.code == "provision_confirm_required"
    description = await service.describe(project["id"])
    decision = description["workspaces"][0]["runtime"]["decision"]
    assert decision["reason"] == "sandbox_lost" and decision["backend"] == "daytona"
    _, _, runtime = await service.rows(project["id"])
    assert runtime.status == "unavailable"
    # Wrong generation is rejected as usual.
    with pytest.raises(ProjectWorkspaceError, match="changed"):
        await service.confirm_provision(project["id"], "local", binding.runtime_generation - 1)
    service.runtime_root = tmp_path / "managed-runtimes"
    recovered = await service.confirm_provision(project["id"], "local", description["workspaces"][0]["runtime"]["generation"])
    assert recovered.execution.backend == "local"
    assert (Path(recovered.execution.locator) / "file.txt").read_text() == "original"
    description = await service.describe(project["id"])
    assert description["workspaces"][0]["runtime"]["decision"] is None
    assert description["workspaces"][0]["runtime"]["status"] == "ready"
    # A workspace without a pending decision rejects further confirmations.
    with pytest.raises(ProjectWorkspaceError, match="does not need recovery"):
        await service.confirm_provision(project["id"], "local", description["workspaces"][0]["runtime"]["generation"])
    service.active.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["NotFoundError", "ConflictError"])
async def test_finished_modal_runtime_returns_recovery_decision(project_service, monkeypatch, error_name):
    from fastapi import HTTPException
    import agent.modules.projects as projects
    import agent.modules.workspaces as workspaces
    from agent.delivery.http.dashboard.routes.projects import ProjectRuntimeBody, ensure_project_runtime
    from agent.modules.workspaces import modal_backend

    modal_errors = pytest.importorskip("modal.exception")
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    _, workspace, _ = await service.rows(project["id"])
    saved_revision = workspace.head_revision
    expired = WorkspaceRef(backend="modal", locator="sb-expired", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        await session.commit()

    execute = AsyncMock(side_effect=getattr(modal_errors, error_name)("sandbox has already finished"))
    backend = modal_backend.ModalWorkspaceBackend(
        expired, sandbox=SimpleNamespace(exec=SimpleNamespace(aio=execute)),
    )
    monkeypatch.setattr(modal_backend.ModalWorkspaceBackend, "create", AsyncMock(return_value=backend))
    monkeypatch.setattr(modal_backend, "_config", lambda: (True, "", "", "configured-app", "/workspace", "", 3600, 900))
    monkeypatch.setattr(workspaces, "attach_workspace_backend", lambda backend, locator, **kwargs:
                        modal_backend.attach_modal_workspace(locator, **kwargs))
    create = AsyncMock()
    monkeypatch.setattr(workspaces, "create_workspace_backend", create)
    monkeypatch.setattr(projects, "get_project_workspace_service", lambda: service)

    for _ in range(2):
        with pytest.raises(HTTPException) as caught:
            await ensure_project_runtime(ProjectRuntimeBody(), project_id=project["id"])
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "provision_confirm_required"
    create.assert_not_awaited()
    execute.assert_awaited_once()
    _, workspace, runtime = await service.rows(project["id"])
    assert runtime.status == "unavailable"
    assert runtime.generation == binding.runtime_generation
    assert workspace.head_revision == saved_revision
    assert json.loads(runtime.pending_decision_json)["reason"] == "sandbox_lost"
    assert (await service.read_saved(project["id"], "file.txt"))[0] == b"original"
    service.active.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_lazy_cloud_chat_and_saved_reads_do_not_initialize_execution(project_service, monkeypatch, pending):
    from agent.delivery.http.api.router import _apply_workspace_to_run_params
    from agent.delivery.http.api.schemas import ChatRequest
    from agent.delivery.http.dashboard.routes.workspace import WorkspaceResolveBody, resolve_dashboard_workspace
    from agent.modules.agent_runtime.workspace_admission import workspace_run
    from agent.modules.skills.package_io import WorkspacePackageIO
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    (root / "AGENTS.md").write_text("Use the saved instructions.")
    await service.ensure(project["id"])
    service.active.clear()
    service.container._async_session_maker = maker
    expired = WorkspaceRef(backend="modal", locator="sb-expired", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        if pending:
            runtime.status = "unavailable"
            runtime.pending_decision_json = json.dumps({"reason": "sandbox_lost", "backend": "modal"})
        await session.commit()
    attach = AsyncMock(side_effect=AssertionError("Reading saved files must not attach execution."))
    create = AsyncMock(side_effect=AssertionError("Opening a conversation must not allocate execution."))
    monkeypatch.setattr(workspaces, "attach_workspace_backend", attach)
    monkeypatch.setattr(workspaces, "create_workspace_backend", create)
    binding = await service.binding(project["id"])
    for existing in [False, True]:
        thread_id = "api_lazy_existing" if existing else "api_lazy_new"
        if existing:
            await workspaces.remember_thread_workspace_ref(thread_id, binding)
        request = ChatRequest(message="Hello", user_id="dashboard", thread_id=thread_id if existing else None,
                              workspace=None if existing else binding, project_id=project["id"])
        params = {"thread_id": thread_id}
        await _apply_workspace_to_run_params(request, params)
        async with workspace_run(params):
            assert params["workspace"]["project_workspace_id"] == binding.project_workspace_id
            assert params["workspace"]["execution"]["locator"] == "sb-expired"
            assert service.switcher.runs[binding.project_workspace_id] == 1
        assert not service.switcher.runs
    resolved = await resolve_dashboard_workspace(None, WorkspaceResolveBody(project_id=project["id"]))
    assert resolved["workspace"]["execution"]["locator"] == "sb-expired"
    transport = WorkspacePackageIO(binding)
    content, _ = await transport.read("AGENTS.md")
    assert content == b"Use the saved instructions."
    with pytest.raises(ValueError, match="escapes"):
        await transport.read("../AGENTS.md")
    scoped = WorkspacePackageIO(binding, boundary=".agents/skills/demo")
    with pytest.raises(ValueError, match="selected skill package"):
        await scoped.read("file.txt")
    browser = await workspaces.get_workspace_browser(binding)
    tree = await browser.tree("/workspace")
    assert tree["root"] == tree["path"] == "/workspace"
    assert any(entry["path"] == "/workspace/AGENTS.md" for entry in tree["entries"])
    assert (await browser.file("/workspace/file.txt"))["content"] == "original"
    assert (await browser.changes())["is_git_repo"] is False
    diff = await browser.diff("/workspace/file.txt")
    assert diff["path"] == "/workspace/file.txt" and diff["is_git_repo"] is False
    with pytest.raises(ValueError, match="escapes"):
        await browser.file("/workspace/../outside")
    assert (await service.binding(project["id"])).runtime_generation == binding.runtime_generation
    assert root.is_dir()
    attach.assert_not_awaited()
    create.assert_not_awaited()
    service.active.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_lazy_concurrent_execution_restores_one_shared_cloud_runtime(project_service, monkeypatch, tmp_path, pending):
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    before = await service.ensure(project["id"])
    service.active.clear()
    expired = WorkspaceRef(backend="modal", locator="sb-expired", metadata={"root": "/workspace"})
    replacement = expired.model_copy(update={"locator": "sb-restored"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        if pending:
            runtime.status = "unavailable"
            runtime.pending_decision_json = json.dumps({"reason": "sandbox_lost", "backend": "modal"})
        await session.commit()
    original_bool = service.container.config_service.get_bool
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False:
                        True if key == "workspace.modal.enabled" else original_bool(key, default))
    async def attach(backend, locator, **kwargs):
        if locator == expired.locator:
            raise WorkspaceUnavailableError("sandbox has already finished")
        assert locator == replacement.locator
        return replacement
    restored = tmp_path / "restored"
    async def restore(ref, store, key, revision):
        assert (await service.rows(project["id"]))[2].status == "restoring"
        await asyncio.to_thread(service.transfer.materialize, store, key, revision, restored)
    create = AsyncMock(return_value=replacement)
    transfer = AsyncMock(side_effect=restore)
    monkeypatch.setattr(workspaces, "attach_workspace_backend", attach)
    monkeypatch.setattr(workspaces, "create_workspace_backend", create)
    monkeypatch.setattr(service.transfer, "restore", transfer)
    results = await asyncio.gather(*(service.ensure(project["id"], recover=True) for _ in range(4)))
    assert all(result.execution.locator == "sb-restored" for result in results)
    assert all(result.project_workspace_id == before.project_workspace_id for result in results)
    assert all(result.runtime_generation == before.runtime_generation + 1 for result in results)
    assert all(result.scope.key == before.scope.key for result in results)
    create.assert_awaited_once_with("modal", label=expired.label)
    transfer.assert_awaited_once()
    assert (restored / "file.txt").read_text() == (root / "file.txt").read_text() == "original"
    _, workspace, runtime = await service.rows(project["id"])
    assert runtime.status == "ready" and runtime.pending_decision_json == "{}"
    assert workspace.persistence_status == "clean"
    service.active.clear()


@pytest.mark.asyncio
async def test_lazy_execution_refuses_disabled_backend_and_missing_saved_data(project_service, monkeypatch):
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    await service.ensure(project["id"])
    service.active.clear()
    expired = WorkspaceRef(backend="modal", locator="sb-expired", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        await session.commit()
    attach = AsyncMock(side_effect=WorkspaceUnavailableError("sandbox has already finished"))
    create = AsyncMock()
    monkeypatch.setattr(workspaces, "attach_workspace_backend", attach)
    monkeypatch.setattr(workspaces, "create_workspace_backend", create)
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False: False)
    with pytest.raises(ProjectWorkspaceError) as disabled:
        await service.ensure(project["id"], recover=True)
    assert disabled.value.code == "backend_disabled"
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False: True)
    async with maker() as session:
        workspace = (await session.execute(select(ProjectWorkspace))).scalar_one()
        workspace.head_revision = "a" * 64
        await session.commit()
    with pytest.raises(ProjectWorkspaceError) as missing:
        await service.ensure(project["id"], recover=True)
    assert missing.value.code == "missing_data"
    create.assert_not_awaited()
    assert (await service.binding(project["id"])).execution.backend == "modal"
    service.active.clear()


@pytest.mark.asyncio
async def test_lazy_failed_restore_keeps_saved_files_and_never_replays_commands(project_service, monkeypatch, tmp_path):
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    before = await service.ensure(project["id"])
    service.active.clear()
    _, workspace, _ = await service.rows(project["id"])
    revision = workspace.head_revision
    manifest = service.storage.get_manifest(str(workspace.id), revision)
    expired = WorkspaceRef(backend="modal", locator="sb-expired", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        await session.commit()
    original_bool = service.container.config_service.get_bool
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False:
                        True if key == "workspace.modal.enabled" else original_bool(key, default))
    monkeypatch.setattr(workspaces, "attach_workspace_backend", AsyncMock(side_effect=WorkspaceUnavailableError("expired")))
    create = AsyncMock(side_effect=[expired.model_copy(update={"locator": name}) for name in ["sb-partial", "sb-ready"]])
    monkeypatch.setattr(workspaces, "create_workspace_backend", create)
    cleanup = AsyncMock()
    monkeypatch.setattr(workspaces, "call_workspace_backend_loader", cleanup)
    execute = AsyncMock(return_value=workspaces.CommandResult("done", 0))
    monkeypatch.setattr(workspaces, "create_workspace_runtime_backend", AsyncMock(
        side_effect=lambda ref, **kwargs: SimpleNamespace(ref=ref, execute=execute)))
    attempts = []
    async def restore(ref, store, key, saved_revision):
        attempts.append(ref.locator)
        if len(attempts) == 1:
            raise OSError("injected restore failure")
        await asyncio.to_thread(service.transfer.materialize, store, key, saved_revision, tmp_path / "restored-after-retry")
    monkeypatch.setattr(service.transfer, "restore", restore)
    monkeypatch.setattr(service.transfer, "capture", AsyncMock(return_value=manifest))
    executor = await workspaces.get_workspace_command_executor(await service.binding(project["id"]))
    try:
        with pytest.raises(ProjectWorkspaceError, match="restore failed"):
            await executor.execute("requested command")
        execute.assert_not_awaited()
        cleanup.assert_awaited_once()
        assert create.await_count == 1
        assert (await service.rows(project["id"]))[1].head_revision == revision
        assert (await service.read_saved(project["id"], "file.txt"))[0] == b"original"
        result = await executor.execute("requested command")
        assert result.exit_code == 0 and result.persistence["saved"]
        execute.assert_awaited_once_with("requested command")
        assert attempts == ["sb-partial", "sb-ready"]
        after = await service.binding(project["id"])
        assert after.runtime_generation == before.runtime_generation + 2
        assert after.project_workspace_id == before.project_workspace_id
        assert (root / "file.txt").read_text() == "original"
    finally:
        service.active.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_root", ["/workspace", "/old-workspace"])
async def test_lazy_active_skill_instructions_do_not_restore_cloud_execution(project_service, monkeypatch, tmp_path, previous_root):
    from langchain_core.messages import HumanMessage
    from agent.modules.skills.context import active_context
    from agent.modules.skills.package_worker import pack_directory
    from agent.modules.skills.sources import workspace_key
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    await service.ensure(project["id"])
    service.active.clear()
    expired = WorkspaceRef(backend="modal", locator="sb-expired", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        runtime.status = "unavailable"
        await session.commit()
    binding = await service.binding(project["id"])
    packages = service.container.skill_packages
    source = tmp_path / "pinned-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: Saved skill.\n---\nPinned instructions.")
    version = packages.snapshots.retain(pack_directory(source), "lazy-conversation")
    destination = packages.execution_destination(binding.execution, version)
    active = {"id": "a" * 32, "version": version, "name": "demo", "agent_name": "default",
              "workspace_key": workspace_key(binding.execution), "content": "Pinned instructions.",
              "skill_root": f"{previous_root}/{destination}", "workspace_root": previous_root,
              "source": {"scope": "project", "directory": ".agents/skills/demo"},
              "execution_mode": "snapshot", "package_path": destination, "shell": "/bin/sh"}
    attach = AsyncMock(side_effect=AssertionError("Conversation context must not attach execution."))
    create = AsyncMock(side_effect=AssertionError("Conversation context must not create execution."))
    monkeypatch.setattr(workspaces, "attach_workspace_backend", attach)
    monkeypatch.setattr(workspaces, "create_workspace_backend", create)
    content, retained, _ = await active_context(
        {"messages": [HumanMessage(content="Explain the project", id="question")], "active_skills": {"demo": active}},
        workspace=binding.execution, thread_id="lazy-conversation", agent_name="default", names=[],
        tools=[SimpleNamespace(name="skill")],
    )
    assert "Pinned instructions." in content
    assert retained["demo"]["skill_root"] == f"/workspace/{destination}"
    attach.assert_not_awaited()
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_lazy_cloud_skill_cache_reads_use_execution_not_project_snapshot(project_service, monkeypatch, tmp_path):
    from agent.modules.skills.package_io import WorkspacePackageIO
    from agent.modules.skills.package_worker import run_operation
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    await service.ensure(project["id"])
    service.active.clear()
    cloud = WorkspaceRef(backend="modal", locator="sb-live", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = cloud.model_dump_json()
        await session.commit()
    cloud_root = tmp_path / "cloud-files"
    manifest = cloud_root / ".k41-agent/s/sk-demo/.skill-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"version":"cached"}')
    attach = AsyncMock(return_value=cloud)
    monkeypatch.setattr(workspaces, "attach_workspace_backend", attach)
    async def operation(transport, op, path="", **kwargs):
        assert transport.ref.backend == "modal"
        return await asyncio.to_thread(run_operation, cloud_root, {"op": op, "path": path, **kwargs})
    monkeypatch.setattr(WorkspacePackageIO, "_operation", operation)
    binding = await service.binding(project["id"])
    execution = WorkspacePackageIO(binding, execution=True)
    content, _ = await execution.read(".k41-agent/s/sk-demo/.skill-manifest.json")
    assert content == b'{"version":"cached"}'
    attach.assert_awaited_once()
    with pytest.raises(FileNotFoundError):
        await service.read_saved(project["id"], ".k41-agent/s/sk-demo/.skill-manifest.json")
    service.active.clear()


@pytest.mark.asyncio
async def test_lazy_first_coding_tool_restores_pinned_skill_cache_before_preparation(project_service, monkeypatch, tmp_path):
    from agent.modules.skills.package_io import WorkspacePackageIO
    from agent.modules.skills.package_worker import DEFAULT_LIMITS, pack_directory, run_operation
    from agent.modules.skills.sources import workspace_key
    from agent.modules.tools.coding.adapter import SCHEMAS, prepare_tool_batch
    from agent.modules.tools.coding.models import ToolDefinition
    from agent.modules.tools.coding.service import get_coding_service
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    await service.ensure(project["id"])
    service.active.clear()
    cloud = WorkspaceRef(backend="modal", locator="sb-live", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = cloud.model_dump_json()
        await session.commit()
    binding = await service.binding(project["id"])
    packages = service.container.skill_packages
    source = tmp_path / "pinned-package"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: Saved skill.\n---\nRead the bundled asset.")
    (source / "asset.bin").write_bytes(b"\x00\xff")
    version = packages.snapshots.retain(pack_directory(source), "lazy-tool")
    destination = packages.execution_destination(binding.execution, version)
    entry = {"id": "a" * 32, "version": version, "name": "demo", "agent_name": "default",
             "workspace_key": workspace_key(binding.execution), "content": "Pinned instructions.",
             "skill_root": f"/workspace/{destination}", "workspace_root": "/workspace",
             "source": {"scope": "project", "directory": ".agents/skills/demo"},
             "execution_mode": "snapshot", "package_path": destination, "shell": "/bin/sh"}
    cloud_root = tmp_path / "cloud-execution"
    cloud_root.mkdir()
    (cloud_root / "file.txt").write_text("original")
    attach = AsyncMock(return_value=cloud)
    monkeypatch.setattr(workspaces, "attach_workspace_backend", attach)
    async def operation(transport, op, path="", **kwargs):
        return await asyncio.to_thread(run_operation, cloud_root,
                                      {"op": op, "path": path, "limits": DEFAULT_LIMITS, **kwargs})
    monkeypatch.setattr(WorkspacePackageIO, "_operation", operation)
    capture = service.transfer.capture
    async def capture_cloud(ref, store, key):
        return await capture(WorkspaceRef(locator=str(cloud_root)), store, key)
    monkeypatch.setattr(service.transfer, "capture", capture_cloud)
    coding = get_coding_service(service.container)
    prepared = AsyncMock(return_value=None)
    monkeypatch.setattr(coding, "prepare_batch", prepared)
    runtime = SimpleNamespace(context={"workspace": binding.execution, "agent_name": "default"},
                              state={"active_skills": {"demo": entry}},
                              config={"configurable": {"thread_id": "api_lazy_tool"}})
    definition = ToolDefinition("bash", "Execute", SCHEMAS["bash"], AsyncMock())
    assert not (cloud_root / destination).exists()
    attach.assert_not_awaited()
    try:
        assert await prepare_tool_batch([SimpleNamespace(name="bash", coding_definition=definition)],
                                        [{"id": "first-tool", "name": "bash", "args": {"command": "echo ready"}}], runtime) is None
        assert (cloud_root / destination / "asset.bin").read_bytes() == b"\x00\xff"
        prepared.assert_awaited_once()
        context = prepared.call_args.args[0][0].context
        assert context.skill_roots == (f"/workspace/{destination}",)
        assert context.locator == "sb-live"
        definition.execute.assert_not_awaited()
    finally:
        service.active.clear()
        await coding.processes.close()
        await coding.storage.close()


@pytest.mark.asyncio
async def test_provision_confirmation_refuses_disabled_backend(project_service, monkeypatch):
    service, project, root, maker = project_service
    await service.ensure(project["id"])
    expired = WorkspaceRef(backend="modal", locator="expired", metadata={"root": "/workspace"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        await session.commit()
    async def attach(backend, locator, **kwargs):
        raise WorkspaceUnavailableError("sandbox is destroyed")
    import agent.modules.workspaces as runtime_service
    monkeypatch.setattr(runtime_service, "attach_workspace_backend", attach)
    with pytest.raises(ProjectWorkspaceError) as blocked:
        await service.ensure(project["id"])
    assert blocked.value.code == "provision_confirm_required"
    _, _, runtime = await service.rows(project["id"])
    with pytest.raises(ProjectWorkspaceError) as refused:
        await service.confirm_provision(project["id"], "restore", runtime.generation)
    assert refused.value.code == "backend_disabled"


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
    # The first tool restores the selected backend before authorization.
    original_bool = service.container.config_service.get_bool
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False:
                        True if key == "workspace.modal.enabled" else original_bool(key, default))
    coding = CodingService(tmp_path / "coding")
    prepared = []
    async def prepare(operation, values, context):
        assert context.locator == "replacement"
        assert context.workspace == "/workspace"
        assert context.project_workspace_id == binding.project_workspace_id
        assert context.runtime_generation == binding.runtime_generation + 1
        assert (restored / "file.txt").read_text() == "original"
        prepared.append(context)

    async def prepare_batch(invocations):
        for operation, values, context in invocations:
            await prepare(operation, values, context)

    monkeypatch.setattr(coding.remote, "prepare_batch", prepare_batch)
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
async def test_github_prune_preserves_host_worktree_until_saved_and_released(project_service, status):
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
    assert await github.prune_orphaned_worktrees() == 0
    await service.release_task_runtime(binding)
    assert await github.prune_orphaned_worktrees() == 1
    assert not task_root.exists()
    saved, _ = await service.read_saved(project["id"], "file.txt", binding.project_workspace_id)
    assert saved == b"unsaved changes"


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
@pytest.mark.parametrize("backend", ["modal", "daytona"])
async def test_restart_detects_expired_cloud_checkpoint_and_restores_on_demand(
    project_service, monkeypatch, tmp_path, caplog, backend,
):
    import agent.modules.workspaces as workspaces

    service, project, root, maker = project_service
    before = await service.ensure(project["id"])
    _, saved, _ = await service.rows(project["id"])
    expired = WorkspaceRef(backend=backend, locator="expired", metadata={"root": "/workspace"})
    replacement = expired.model_copy(update={"locator": "replacement"})
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = expired.model_dump_json()
        await session.commit()
    service.active.clear()
    restarted = ProjectWorkspaceService(service.container, storage=service.storage)
    unavailable = WorkspaceUnavailableError("Sandbox has already finished", backend=backend, locator=expired.locator)
    remote = AsyncMock(side_effect=unavailable)
    monkeypatch.setattr(restarted.transfer, "_backend", remote)
    create = AsyncMock(return_value=replacement)
    attach = AsyncMock(return_value=replacement)
    monkeypatch.setattr(workspaces, "create_workspace_backend", create)
    monkeypatch.setattr(workspaces, "attach_workspace_backend", attach)
    original_bool = service.container.config_service.get_bool
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False:
                        True if key == f"workspace.{backend}.enabled" else original_bool(key, default))
    completed = asyncio.Event()
    decision = restarted._record_provision_decision
    async def record_decision(*args):
        await decision(*args)
        completed.set()
    monkeypatch.setattr(restarted, "_record_provision_decision", record_decision)
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_runtime_status", AsyncMock(return_value="destroyed"))
    restored = tmp_path / "restored"

    async def restore(ref, store, key, revision):
        assert (await restarted.rows(project["id"]))[2].status == "restoring"
        await asyncio.to_thread(restarted.transfer.materialize, store, key, revision, restored)

    transfer = AsyncMock(side_effect=restore)
    monkeypatch.setattr(restarted.transfer, "restore", transfer)
    caplog.set_level("INFO", logger="agent.modules.projects.workspaces")
    restarted.checkpoint_requested.set()
    restarted.start()
    try:
        await asyncio.wait_for(completed.wait(), timeout=10)
        assert before.project_workspace_id not in restarted.active
        _, workspace, runtime = await restarted.rows(project["id"])
        assert runtime.status == "unavailable"
        assert runtime.generation == before.runtime_generation
        assert json.loads(runtime.pending_decision_json)["reason"] == "sandbox_lost"
        assert workspace.head_revision == saved.head_revision
        assert workspace.previous_revision == saved.previous_revision
        assert workspace.last_saved_at == saved.last_saved_at
        assert workspace.persistence_status == "clean"
        assert (await restarted.read_saved(project["id"], "file.txt"))[0] == b"original"
        create.assert_not_awaited()
        attach.assert_not_awaited()
        remote.assert_not_awaited()
        assert not any(record.exc_info for record in caplog.records if record.name == "agent.modules.projects.workspaces")
        await restarted.close()

        # A later host restart must not resume checkpointing the expired runtime.
        completed.clear()
        initialized = asyncio.Event()
        wait_for_checkpoint = restarted.checkpoint_requested.wait

        async def wait_after_initialization():
            initialized.set()
            await wait_for_checkpoint()

        monkeypatch.setattr(restarted.checkpoint_requested, "wait", wait_after_initialization)
        restarted.start()
        await asyncio.wait_for(initialized.wait(), timeout=10)
        assert before.project_workspace_id not in restarted.active
        assert not completed.is_set()
        remote.assert_not_awaited()
        create.assert_not_awaited()

        bindings = await asyncio.gather(*(restarted.ensure(project["id"], recover=True) for _ in range(4)))
        assert all(binding.project_workspace_id == before.project_workspace_id for binding in bindings)
        assert all(binding.runtime_generation == before.runtime_generation + 1 for binding in bindings)
        assert all(binding.execution.backend == backend and binding.execution.locator == replacement.locator for binding in bindings)
        create.assert_awaited_once_with(backend, label=expired.label)
        transfer.assert_awaited_once()
        assert (restored / "file.txt").read_bytes() == b"original"
        _, workspace, runtime = await restarted.rows(project["id"])
        assert runtime.status == "ready" and runtime.pending_decision_json == "{}"
        assert workspace.persistence_status == "clean" and workspace.last_error == ""
    finally:
        restarted.active.clear()
        await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("RPC timed out"), OSError("Connection lost"), FileNotFoundError("Project entry disappeared")])
async def test_transient_cloud_checkpoint_failure_keeps_runtime_for_retry(project_service, monkeypatch, error):
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    _, saved, _ = await service.rows(project["id"])
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime))).scalar_one()
        runtime.execution_json = WorkspaceRef(backend="modal", locator="live", metadata={"root": "/workspace"}).model_dump_json()
        await session.commit()
    monkeypatch.setattr(service.transfer, "_backend", AsyncMock(side_effect=error))
    with pytest.raises(ProjectWorkspaceError) as failure:
        await service.checkpoint(project["id"], skip_unchanged=True)
    assert failure.value.code == "persistence_error"
    assert binding.project_workspace_id in service.active
    _, workspace, runtime = await service.rows(project["id"])
    assert runtime.status == "ready" and runtime.pending_decision_json == "{}"
    assert workspace.head_revision == saved.head_revision and workspace.last_saved_at == saved.last_saved_at
    service.active.clear()


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
async def test_missing_checkpoint_root_stops_monitoring_and_retry_preserves_saved_files(project_service):
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    _, before, _ = await service.rows(project["id"])
    offline = root.with_name("offline")
    root.rename(offline)
    try:
        with pytest.raises(ProjectWorkspaceError) as failure:
            await service.checkpoint(project["id"])
        assert failure.value.code == "runtime_unavailable"
        assert str(root) in str(failure.value)
        assert binding.project_workspace_id not in service.active
        _, after, _ = await service.rows(project["id"])
        assert after.head_revision == before.head_revision
        assert after.last_saved_at == before.last_saved_at
        assert after.persistence_status == "error"
        assert after.last_error == str(failure.value)
        assert (await service.read_saved(project["id"], "file.txt"))[0] == b"original"
        assert not root.exists()
    finally:
        offline.rename(root)
    (root / "file.txt").write_bytes(b"recovered changes")
    await service.checkpoint(project["id"])
    assert binding.project_workspace_id in service.active
    assert (await service.describe(project["id"]))["persistence"]["status"] == "clean"
    assert (await service.read_saved(project["id"], "file.txt"))[0] == b"recovered changes"


@pytest.mark.asyncio
async def test_missing_project_entry_does_not_disable_checkpoint_retries(project_service, monkeypatch):
    service, project, root, maker = project_service
    binding = await service.ensure(project["id"])
    monkeypatch.setattr(service.transfer, "capture", AsyncMock(side_effect=FileNotFoundError("Project entry disappeared")))
    with pytest.raises(ProjectWorkspaceError) as failure:
        await service.checkpoint(project["id"])
    assert failure.value.code == "persistence_error"
    assert binding.project_workspace_id in service.active


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
@pytest.mark.parametrize("phase", ["resolve", "submit"])
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
        find.side_effect = error
    monkeypatch.setattr(projects, "get_project_workspace_service", lambda: SimpleNamespace(find_binding=find, ensure=ensure))
    submit = AsyncMock(side_effect=error if phase == "submit" else None)
    monkeypatch.setattr(tasks, "get_background_task_manager", lambda: SimpleNamespace(submit=submit))
    with pytest.raises(HTTPException) as failure:
        await tasks.submit_background_task(tasks.SubmitTaskBody(request="Work", workspace=binding.execution if phase == "submit" else binding))
    assert failure.value.status_code == status
    ensure.assert_not_awaited()
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


@pytest_asyncio.fixture
async def switch_service(project_service, monkeypatch):
    import shlex
    from pathlib import PurePosixPath
    import agent.modules.workspaces as workspace_api
    from agent.modules.projects import snapshot_worker
    from agent.modules.tools.coding.remote import RemoteCodingRuntime
    service, project, root, maker = project_service
    await service.ensure(project["id"])
    service.runtime_root = root.parent / "managed-runtimes"
    roots = {}
    async def running(ref):
        return ref.backend == "local" or ref.locator in roots
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_is_running", running)
    async def status(ref):
        return "started" if await running(ref) else "destroyed"
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_runtime_status", status)
    created, retired = [], []
    original_bool = service.container.config_service.get_bool
    monkeypatch.setattr(service.container.config_service, "get_bool", lambda key, default=False:
                        True if key in {"workspace.daytona.enabled", "workspace.modal.enabled"} else original_bool(key, default))
    monkeypatch.setattr(service.switcher, "_assert_no_interrupts", AsyncMock())
    monkeypatch.setattr(RemoteCodingRuntime, "workspace_processes", AsyncMock(return_value=0))

    async def create(backend, *, label=None, on_created=None):
        locator = backend + "-" + str(len(roots))
        directory = root.parent / locator
        directory.mkdir()
        roots[locator] = directory
        ref = WorkspaceRef(backend=backend, locator=locator, label=label or backend, metadata={"root": "/workspace"})
        created.append(ref)
        if on_created:
            await on_created(ref)
        return ref

    async def attach(backend, locator, *, label=None, root=None):
        return WorkspaceRef(backend=backend, locator=locator, label=label or backend, metadata={"root": root or "/workspace"})

    async def lifecycle(backend, loader, ref, **kwargs):
        retired.append(ref)

    class Backend:
        def __init__(self, ref):
            self.root = roots[ref.locator].as_posix()

        def stage(self, remote):
            return root.parent / "transfers" / Path(*PurePosixPath(remote).relative_to("/tmp").parts)

        async def upload_coding_file(self, content, path):
            self.stage(path).write_bytes(content)

        async def download_coding_file(self, path):
            return self.stage(path).read_bytes()

        async def execute_coding_command(self, command, **kwargs):
            args = shlex.split(command)
            if args[0] == "mkdir":
                self.stage(args[-1]).mkdir(parents=True, exist_ok=True)
            else:
                _, _, operation, destination, stage = args
                stage = str(self.stage(stage))
                if operation == "capture":
                    snapshot_worker.capture(destination, stage)
                elif operation == "restore":
                    snapshot_worker.restore(destination, stage)
                elif operation == "verify":
                    snapshot_worker.verify(destination, json.loads((Path(stage) / "manifest.json").read_text()))
            return SimpleNamespace(exit_code=0, output="")

    async def backend(ref):
        return Backend(ref)

    monkeypatch.setattr(workspace_api, "create_workspace_backend", create)
    monkeypatch.setattr(workspace_api, "attach_workspace_backend", attach)
    monkeypatch.setattr(workspace_api, "call_workspace_backend_loader", lifecycle)
    monkeypatch.setattr(service.transfer, "_backend", backend)
    return SimpleNamespace(service=service, project=project, root=root, maker=maker, roots=roots,
                           created=created, retired=retired, create=create)


async def finish_switch(fixture, backend):
    binding = await fixture.service.binding(fixture.project["id"])
    result = await fixture.service.switcher.request(fixture.project["id"], backend, binding.runtime_generation)
    job = fixture.service.switcher.tasks.get(binding.project_workspace_id)
    if job:
        await job
    return result, await fixture.service.binding(fixture.project["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("representation", ["reference", "dictionary", "path", "binding", "binding_dictionary"])
async def test_unmanaged_local_file_io_accepts_supported_workspace_inputs(project_service, representation):
    from agent.modules.workspaces import get_workspace_file_io
    _, _, root, _ = project_service
    temporary = root.parent / "temporary"
    temporary.mkdir()
    (temporary / "file.txt").write_text("original")
    ref = WorkspaceRef(locator=str(temporary), metadata={"temp": True})
    binding = bind_workspace_ref(ref)
    workspace = {"reference": ref, "dictionary": ref.model_dump(), "path": str(temporary),
                 "binding": binding, "binding_dictionary": binding.model_dump()}[representation]
    backend = await get_workspace_file_io(workspace, thread_id="temporary-thread")
    assert await backend.read_text("file.txt") == "original"
    await backend.write_text("file.txt", "updated")
    assert (temporary / "file.txt").read_text() == "updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("representation", ["reference", "dictionary", "binding", "binding_dictionary"])
async def test_unmanaged_cloud_file_io_normalizes_workspace_inputs(project_service, monkeypatch, representation):
    import agent.modules.workspaces as workspaces
    ref = WorkspaceRef(backend="daytona", locator="temporary-sandbox", metadata={"root": "/workspace", "temp": True})
    binding = bind_workspace_ref(ref)
    workspace = {"reference": ref, "dictionary": ref.model_dump(), "binding": binding,
                 "binding_dictionary": binding.model_dump()}[representation]
    factory = AsyncMock(return_value=SimpleNamespace(read_text=AsyncMock(return_value="live content")))
    monkeypatch.setattr(workspaces, "create_workspace_runtime_backend", factory)
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_is_running", AsyncMock(return_value=True))
    backend = await workspaces.get_workspace_file_io(workspace, thread_id="temporary-thread")
    assert await backend.read_text("file.txt") == "live content"
    execution = factory.await_args.args[0]
    assert execution.backend == ref.backend and execution.locator == ref.locator
    assert execution.metadata["root"] == "/workspace" and execution.metadata["_require_running"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider_status", "runtime_status"), [
    ("destroyed", "unavailable"), ("stopped", "stopped"), ("destroyed", "ready"),
])
async def test_saved_host_conflicts_can_be_resolved_before_local_recovery(
    switch_service, monkeypatch, provider_status, runtime_status,
):
    fixture, service = switch_service, switch_service.service
    await finish_switch(fixture, "daytona")
    (fixture.root / "file.txt").write_text("host edit")
    project, workspace, runtime = await service.rows(fixture.project["id"])
    if runtime_status == "unavailable":
        await service._record_provision_decision(project, workspace, runtime)
    elif runtime_status == "stopped":
        async with fixture.maker() as session:
            record = await session.get(WorkspaceRuntime, runtime.id)
            record.status = runtime_status
            await session.commit()
    state = await service.host_mirrors.after_checkpoint(workspace, runtime, workspace.head_revision)
    assert state["status"] == "conflict"
    original_capture = service.transfer.capture
    async def capture(ref, *args):
        assert ref.backend == "local", "Offline resolution must only capture the host backup"
        return await original_capture(ref, *args)
    with monkeypatch.context() as patch:
        patch.setattr("agent.modules.workspaces.host_mirror.workspace_runtime_status", AsyncMock(return_value=provider_status))
        patch.setattr(service.transfer, "capture", capture)
        checkpoint = AsyncMock(side_effect=AssertionError("Offline resolution must not checkpoint the sandbox"))
        patch.setattr(service, "_checkpoint", checkpoint)
        with pytest.raises(ProjectWorkspaceError) as stale:
            await service.host_mirrors.resolve_project(project.id, workspace.id, runtime.generation + 1,
                                                      "keep_sandbox", state["resolution_token"])
        assert stale.value.code == "stale_runtime"
        service.switcher.switching.add(workspace.id)
        try:
            with pytest.raises(ProjectWorkspaceError) as busy:
                await service.host_mirrors.resolve_project(project.id, workspace.id, runtime.generation,
                                                          "keep_sandbox", state["resolution_token"])
            assert busy.value.code == "workspace_switching"
        finally:
            service.switcher.switching.discard(workspace.id)
        with pytest.raises(ProjectWorkspaceError) as stale:
            await service.host_mirrors.resolve_project(project.id, workspace.id, runtime.generation,
                                                      "keep_sandbox", "0" * 64)
        assert stale.value.code == "stale_content"
        assert (fixture.root / "file.txt").read_text() == "host edit"
        if runtime_status != "ready":
            with pytest.raises(ProjectWorkspaceError) as unavailable:
                await service.host_mirrors.resolve_project(project.id, workspace.id, runtime.generation,
                                                          "keep_host", state["resolution_token"])
            assert unavailable.value.code == "runtime_unavailable"
        resolved = await service.host_mirrors.resolve_project(project.id, workspace.id, runtime.generation,
                                                            "keep_sandbox", state["resolution_token"])
        checkpoint.assert_not_awaited()
    assert resolved["status"] == "synced" and (fixture.root / "file.txt").read_text() == "original"
    project, workspace, runtime = await service.rows(project.id, workspace.id)
    assert workspace.persistence_status == "clean" and runtime.status == runtime_status
    backup = service.storage.get_manifest(str(workspace.id), json.loads(workspace.host_mirror_json)["host_backup_revision"])
    assert service.storage.get_blob(str(workspace.id), backup["entries"]["file.txt"]["checksum"]) == b"host edit"
    await service._record_provision_decision(project, workspace, runtime)
    recovered = await service.confirm_provision(project.id, "local", runtime.generation, workspace.id)
    assert recovered.execution.backend == "local" and recovered.execution.locator == str(fixture.root)


@pytest.mark.asyncio
async def test_temporary_capture_survives_host_failure_and_provider_deletion(switch_service, monkeypatch, tmp_path):
    fixture, service = switch_service, switch_service.service
    ref = await fixture.create("daytona")
    ref.metadata["temp"] = True
    remote = fixture.roots[ref.locator]
    (remote / "file.txt").write_text("original")
    async with fixture.maker() as session:
        session.add(ThreadWorkspace(thread_id="failed-mirror", execution_backend=ref.backend, execution_locator=ref.locator,
                                    execution_metadata_json=json.dumps(ref.metadata)))
        await session.commit()
    monkeypatch.setattr("agent.modules.workspaces.service.temp_workspace_root", lambda: tmp_path / "temporary")
    monkeypatch.setattr(service.host_mirrors, "_changes", AsyncMock(return_value={"token": "changed", "changed": True}))
    rows, _ = await service.host_mirrors.thread_status("failed-mirror")
    await service.host_mirrors._poll_threads(rows)
    rows, initial = await service.host_mirrors.thread_status("failed-mirror")
    (remote / "file.txt").write_text("latest sandbox content")
    async def fail_apply(store, key, state, revision, generation):
        _, persisted = await service.host_mirrors.thread_status("failed-mirror")
        assert persisted["remote_revision"] == revision
        raise PermissionError("Host file locked")
    with monkeypatch.context() as patch:
        patch.setattr(service.host_mirrors, "_apply", fail_apply)
        with pytest.raises(ProjectWorkspaceError, match="Host file locked"):
            await service.host_mirrors._poll_threads(rows, force=True)
    rows, failed = await service.host_mirrors.thread_status("failed-mirror")
    assert failed["status"] == "error" and failed["revision"] == initial["revision"]
    assert failed["remote_revision"] != initial["remote_revision"]
    assert (Path(failed["path"]) / "file.txt").read_text() == "original"
    async with service.host_mirrors.saved_thread_view("failed-mirror") as view:
        assert (Path(view.locator) / "file.txt").read_text() == "latest sandbox content"
    def deleted_sandbox(ref):
        raise RuntimeError("Sandbox not found (404)")
    monkeypatch.setattr("agent.modules.workspaces.daytona_backend.get_daytona_sandbox", deleted_sandbox)
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_runtime_status", probe_workspace_runtime_status)
    await service.host_mirrors.switch_thread("failed-mirror", "local", failed["generation"])
    rows, recovered = await service.host_mirrors.thread_status("failed-mirror")
    assert service.host_mirrors.thread_ref(rows[0]).backend == "local"
    assert (Path(recovered["path"]) / "file.txt").read_text() == "latest sandbox content"


@pytest.mark.asyncio
async def test_automatic_host_mirror_keeps_one_directory_and_separate_save_status(switch_service):
    fixture, service = switch_service, switch_service.service
    _, binding = await finish_switch(fixture, "daytona")
    remote = fixture.roots[binding.execution.locator]
    (remote / "created.txt").write_text("sandbox output")
    await service.checkpoint(fixture.project["id"])
    assert (fixture.root / "created.txt").read_text() == "sandbox output"
    (fixture.root / "file.txt").write_text("external host edit")
    (remote / "created.txt").write_text("new sandbox output")
    result = await service.checkpoint(fixture.project["id"])
    assert result["saved"] and result["host_mirror"]["status"] == "conflict"
    assert (fixture.root / "created.txt").read_text() == "sandbox output"
    await finish_switch(fixture, "local")
    assert (await service.binding(fixture.project["id"])).execution.backend == "daytona"
    state = (await service.describe(fixture.project["id"]))["host_mirror"]
    await service.host_mirrors.resolve_project(fixture.project["id"], binding.project_workspace_id,
        binding.runtime_generation, "keep_sandbox", state["resolution_token"])
    _, local = await finish_switch(fixture, "local")
    assert local.execution.locator == str(fixture.root)
    assert (fixture.root / "created.txt").read_text() == "new sandbox output"
    assert (fixture.root / "file.txt").read_text() == "original"
    _, cloud = await finish_switch(fixture, "modal")
    _, local_again = await finish_switch(fixture, "local")
    assert local_again.execution.locator == str(fixture.root)


@pytest.mark.asyncio
async def test_legacy_local_runtime_rebinds_to_original_without_removing_the_old_copy(switch_service):
    import shutil
    fixture, service = switch_service, switch_service.service
    project, workspace, runtime = await service.rows(fixture.project["id"])
    legacy = service.runtime_root / str(workspace.id) / "legacy-generation"
    shutil.copytree(fixture.root, legacy)
    (legacy / "file.txt").write_text("legacy runtime edit")
    await service._publish_runtime(project, workspace, WorkspaceRef(locator=str(legacy)), runtime.generation + 1, "ready")
    _, binding = await finish_switch(fixture, "local")
    assert binding.execution.locator == str(fixture.root)
    assert (fixture.root / "file.txt").read_text() == "legacy runtime edit"
    assert (legacy / "file.txt").read_text() == "legacy runtime edit"
    assert not fixture.created


@pytest.mark.asyncio
async def test_legacy_local_conflict_resolution_preserves_the_approved_host_edit(switch_service):
    import shutil
    fixture, service = switch_service, switch_service.service
    project, workspace, runtime = await service.rows(fixture.project["id"])
    legacy = service.runtime_root / str(workspace.id) / "legacy-generation"
    shutil.copytree(fixture.root, legacy)
    (legacy / "file.txt").write_text("runtime edit")
    (fixture.root / "file.txt").write_text("host edit")
    generation = runtime.generation + 1
    await service._publish_runtime(project, workspace, WorkspaceRef(locator=str(legacy)), generation, "ready")
    saved = await service.checkpoint(project.id)
    assert saved["host_mirror"]["status"] == "conflict"
    state = await service.host_mirrors.resolve_project(project.id, workspace.id, generation,
                                                      "keep_host", saved["host_mirror"]["resolution_token"])
    assert state["status"] == "synced"
    _, binding = await finish_switch(fixture, "local")
    assert binding.execution.locator == str(fixture.root)
    assert (fixture.root / "file.txt").read_text() == (legacy / "file.txt").read_text() == "host edit"


@pytest.mark.asyncio
async def test_workspace_reads_live_files_and_falls_back_without_waking_the_provider(switch_service, monkeypatch):
    import agent.modules.workspaces as workspaces
    from agent.modules.projects.backend import DeferredWorkspaceBackend
    from agent.modules.agent_runtime import get_active_session_registry
    fixture, service = switch_service, switch_service.service
    _, binding = await finish_switch(fixture, "daytona")
    factory = workspaces.create_workspace_runtime_backend
    cloud_reads = []
    async def backend(ref, **kwargs):
        if ref.backend == "local":
            return await factory(ref, **kwargs)
        cloud_reads.append(ref)
        assert ref.metadata["_require_running"]
        return SimpleNamespace(file=AsyncMock(return_value={"content": "live sandbox edit"}))
    monkeypatch.setattr(workspaces, "create_workspace_runtime_backend", backend)
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_is_running", AsyncMock(return_value=False))
    browser = DeferredWorkspaceBackend(binding, service)
    saved = await browser.file("file.txt")
    assert saved["content"] == "original" and saved["stale"] and not cloud_reads
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_is_running", AsyncMock(return_value=True))
    live = await browser.file("file.txt")
    assert live["content"] == "live sandbox edit" and live["data_source"] == "live" and not live["stale"]
    assert len(cloud_reads) == 1
    registry = get_active_session_registry(service.container)
    queue = registry.subscribe()
    try:
        await service.checkpoint(fixture.project["id"])
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["type"] == "workspace_files_changed"
        assert event["data"]["project_workspace_id"] == binding.project_workspace_id
    finally:
        registry.unsubscribe(queue)


@pytest.mark.asyncio
async def test_host_resolution_rejects_stale_content_and_pushes_only_conflicts(switch_service, monkeypatch):
    fixture, service = switch_service, switch_service.service
    _, binding = await finish_switch(fixture, "daytona")
    remote = fixture.roots[binding.execution.locator]
    (remote / "remote-only.txt").write_text("cloud")
    (fixture.root / "file.txt").write_text("host edit")
    await service.checkpoint(fixture.project["id"])
    state = (await service.describe(fixture.project["id"]))["host_mirror"]
    (fixture.root / "file.txt").write_text("newer host edit")
    with pytest.raises(ProjectWorkspaceError) as error:
        await service.host_mirrors.resolve_project(fixture.project["id"], binding.project_workspace_id,
            binding.runtime_generation, "keep_sandbox", state["resolution_token"])
    assert error.value.code == "stale_content"
    await service.checkpoint(fixture.project["id"])
    state = (await service.describe(fixture.project["id"]))["host_mirror"]
    async def reconcile(ref, store, key, expected, desired):
        from agent.modules.projects import snapshot_worker
        import tempfile
        # Run the portable overlay against the simulated provider filesystem.
        with tempfile.TemporaryDirectory(prefix="k41-project-") as stage:
            stage = Path(stage)
            service.transfer.materialize(store, key, store.put_manifest(key, desired), stage / "desired")
            (stage / "expected.json").write_text(json.dumps(expected))
            (stage / "manifest.json").write_text(json.dumps(desired))
            (stage / "objects").mkdir()
            from agent.modules.projects.storage import file_chunks
            for entry in desired["entries"].values():
                if entry["type"] == "file":
                    for chunk in file_chunks(entry):
                        (stage / "objects" / chunk["checksum"]).write_bytes(store.get_blob(key, chunk["checksum"]))
            snapshot_worker.overlay(str(remote), str(stage))
    monkeypatch.setattr(service.transfer, "reconcile", reconcile)
    result = await service.host_mirrors.resolve_project(fixture.project["id"], binding.project_workspace_id,
        binding.runtime_generation, "keep_host", state["resolution_token"])
    assert result["status"] == "synced"
    assert (remote / "file.txt").read_text() == "newer host edit"
    assert (fixture.root / "remote-only.txt").read_text() == "cloud"


@pytest.mark.asyncio
async def test_temporary_mirrors_share_aliases_and_keep_the_host_folder_across_switches(switch_service, monkeypatch, tmp_path):
    from agent.modules.workspaces.host_mirror import mirror_state
    from agent.modules.projects.workspaces import source_key
    fixture, service = switch_service, switch_service.service
    ref = await fixture.create("daytona")
    ref.metadata["temp"] = True
    remote = fixture.roots[ref.locator]
    (remote / "file.txt").write_text("sandbox")
    async with fixture.maker() as session:
        for thread_id in ("mirror-main", "mirror-main:sub:child", "mirror-alias"):
            session.add(ThreadWorkspace(thread_id=thread_id, execution_backend=ref.backend, execution_locator=ref.locator,
                                        execution_metadata_json=json.dumps(ref.metadata)))
        await session.commit()
    monkeypatch.setattr("agent.modules.workspaces.service.temp_workspace_root", lambda: tmp_path / "temporary")
    changes = AsyncMock(return_value={"token": "first", "changed": True})
    monkeypatch.setattr(service.host_mirrors, "_changes", changes)
    rows, _ = await service.host_mirrors.thread_status("mirror-main:sub:child")
    await service.host_mirrors._poll_threads(rows)
    rows, state = await service.host_mirrors.thread_status("mirror-main")
    host = Path(state["path"])
    assert len(rows) == 3 and (host / "file.txt").read_text() == "sandbox"
    from agent.modules.agent_runtime.workspace_admission import workspace_run
    from agent.modules.workspaces.host_mirror import thread_storage_key
    async with workspace_run({"thread_id": "mirror-main", "workspace": ref.model_dump()}):
        with pytest.raises(ProjectWorkspaceError) as blocked:
            await service.host_mirrors.switch_thread("mirror-main", "local", state["generation"])
        assert blocked.value.code == "workspace_busy"
    lease_id = -int(thread_storage_key(rows))
    service.switcher.switching.add(lease_id)
    try:
        with pytest.raises(ProjectWorkspaceError) as blocked:
            async with workspace_run({"thread_id": "mirror-main:sub:child", "workspace": ref.model_dump()}):
                pytest.fail("A new child run entered a switching temporary workspace")
        assert blocked.value.code == "workspace_switching"
    finally:
        service.switcher.switching.discard(lease_id)
    assert len({mirror_state(row)["path"] for row in rows}) == 1
    assert len(list((tmp_path / "temporary").iterdir())) == 1
    expired_locator = ref.locator
    async def status(workspace):
        return "destroyed" if workspace.locator == expired_locator else "started"
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_runtime_status", status)
    await service.host_mirrors.switch_thread("mirror-main", "modal", state["generation"])
    rows, state = await service.host_mirrors.thread_status("mirror-main")
    ref = service.host_mirrors.thread_ref(rows[0])
    remote = fixture.roots[ref.locator]
    assert state["path"] == str(host) and (remote / "file.txt").read_text() == "sandbox"
    (host / "file.txt").write_text("external host")
    (remote / "added.txt").write_text("remote addition")
    await service.host_mirrors._poll_threads(rows)
    rows, state = await service.host_mirrors.thread_status("mirror-main")
    assert state["status"] == "conflict" and not (host / "added.txt").exists()
    async with service.host_mirrors.saved_thread_view("mirror-main") as view:
        assert (Path(view.locator) / "file.txt").read_text() == "sandbox"
        assert (Path(view.locator) / "added.txt").read_text() == "remote addition"
    await service.host_mirrors.synchronize_thread("mirror-main", generation=state["generation"],
                                                 action="keep_sandbox", token=state["resolution_token"])
    first_generation = state["generation"]
    await service.host_mirrors.switch_thread("mirror-main", "local", first_generation)
    rows, state = await service.host_mirrors.thread_status("mirror-main")
    assert all(service.host_mirrors.thread_ref(row).locator == str(host) for row in rows)
    assert state["path"] == str(host) and state["generation"] != first_generation
    await service.host_mirrors.switch_thread("mirror-main", "modal", state["generation"])
    rows, state = await service.host_mirrors.thread_status("mirror-main")
    await service.host_mirrors.switch_thread("mirror-main", "local", state["generation"])
    rows, state = await service.host_mirrors.thread_status("mirror-main")
    assert all(service.host_mirrors.thread_ref(row).locator == str(host) for row in rows)
    with pytest.raises(ProjectWorkspaceError) as error:
        await service.host_mirrors.switch_thread("mirror-main", "daytona", first_generation)
    assert error.value.code == "stale_runtime"
    assert (host / "added.txt").read_text() == "remote addition"


@pytest.mark.asyncio
async def test_backend_roundtrip_keeps_one_project_and_local_baseline(switch_service, monkeypatch):
    from agent.modules.projects.repository import register_project
    from agent.modules.workspaces.refs import bind_workspace_ref
    fixture = switch_service
    service = fixture.service
    original = WorkspaceRef(locator=str(fixture.root))
    baseline = (await service.rows(fixture.project["id"]))[1].local_sync_revision
    await catalog.update_project(fixture.project["id"], {"label": "Stable project"})
    old_refs = [original]
    for backend in ("daytona", "modal", "local"):
        _, binding = await finish_switch(fixture, backend)
        assert binding.scope.label == "Stable project"
        old_refs.append(binding.execution)
        target = Path(binding.execution.locator) if backend == "local" else fixture.roots[binding.execution.locator]
        (target / (backend + ".txt")).write_text(backend)
        await service.checkpoint(fixture.project["id"])
    for ref in old_refs:
        async with fixture.maker() as session:
            project = await register_project(session, bind_workspace_ref(ref), only_missing=True)
            assert project.id == fixture.project["id"]
            await session.commit()
    async with fixture.maker() as session:
        assert len((await session.execute(select(Project))).scalars().all()) == 1
        assert len((await session.execute(select(ProjectWorkspace))).scalars().all()) == 1
        assert len((await session.execute(select(WorkspaceRuntime))).scalars().all()) == 1
    workspace = (await service.rows(fixture.project["id"]))[1]
    assert workspace.local_sync_revision == workspace.head_revision
    assert workspace.local_sync_revision != baseline
    description = await service.describe(fixture.project["id"])
    assert description["workspace_name"] == "main"
    assert description["local_link"] == {"path": str(fixture.root), "revision": workspace.head_revision, "status": "active"}
    assert binding.execution.locator == str(fixture.root)
    leftover = fixture.root / (".k41-sync-" + "a" * 32)
    leftover.write_text("interrupted replacement")
    await service.checkpoint(fixture.project["id"])
    assert not leftover.exists()
    assert all((fixture.root / (backend + ".txt")).is_file() for backend in ("daytona", "modal", "local"))
    assert (await service.describe(fixture.project["id"]))["local_link"]["status"] == "active"


@pytest.mark.asyncio
async def test_local_sync_preserves_external_edits_and_rejects_stale_preview(switch_service):
    fixture = switch_service
    service = fixture.service
    _, binding = await finish_switch(fixture, "daytona")
    remote = fixture.roots[binding.execution.locator]
    (remote / "new.bin").write_bytes(bytes(range(256)))
    (remote / "folder").mkdir()
    (remote / "folder" / "file.txt").write_text("new file")
    (fixture.root / "file.txt").write_text("independent local edit")
    (fixture.root / "local-only.txt").write_text("keep")
    (fixture.root / "node_modules").mkdir()
    (fixture.root / "node_modules" / "cached").write_text("keep cache")
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert not preview["conflicts"] and not preview["applied"]
    (fixture.root / "local-only.txt").write_text("newer local edit")
    with pytest.raises(ProjectWorkspaceError) as failure:
        await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert failure.value.code == "stale_content"
    assert not (fixture.root / "new.bin").exists()
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    result = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert result["applied"]
    assert (fixture.root / "file.txt").read_text() == "independent local edit"
    assert (fixture.root / "local-only.txt").read_text() == "newer local edit"
    assert (fixture.root / "node_modules" / "cached").read_text() == "keep cache"
    assert (fixture.root / "new.bin").read_bytes() == bytes(range(256))
    assert (fixture.root / "folder" / "file.txt").read_text() == "new file"


@pytest.mark.asyncio
async def test_local_sync_conflicts_block_all_writes_and_busy_runs(switch_service):
    fixture = switch_service
    service = fixture.service
    _, binding = await finish_switch(fixture, "modal")
    remote = fixture.roots[binding.execution.locator]
    (remote / "file.txt").write_text("remote edit")
    (remote / "additional.txt").write_text("must not be applied on conflict")
    (fixture.root / "file.txt").write_text("local edit")
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert preview["conflicts"] == ["file.txt"]
    with pytest.raises(ProjectWorkspaceError) as failure:
        await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert failure.value.code == "local_sync_conflict"
    assert (fixture.root / "file.txt").read_text() == "local edit"
    assert not (fixture.root / "additional.txt").exists()
    with service.switcher.run_lease(binding.project_workspace_id):
        with pytest.raises(ProjectWorkspaceError) as failure:
            await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert failure.value.code == "workspace_busy"
    assert not service.switcher.switching and not service.switcher.tasks


@pytest.mark.asyncio
async def test_local_sync_blocks_missing_parent_conflicts_before_any_write(switch_service):
    import shutil
    fixture = switch_service
    service = fixture.service
    (fixture.root / "folder").mkdir()
    (fixture.root / "folder" / "original.txt").write_text("original")
    _, binding = await finish_switch(fixture, "daytona")
    remote = fixture.roots[binding.execution.locator]
    (remote / "folder" / "new.txt").write_text("cloud addition")
    (remote / "file.txt").write_text("cloud edit")
    shutil.rmtree(fixture.root / "folder")
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert preview["conflicts"] == ["folder"]
    with pytest.raises(ProjectWorkspaceError) as failure:
        await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert failure.value.code == "local_sync_conflict"
    assert (fixture.root / "file.txt").read_text() == "original"
    assert not (fixture.root / "folder").exists()


@pytest.mark.asyncio
async def test_local_sync_saves_baseline_after_applying_cloud_permissions_on_windows(switch_service, monkeypatch):
    import agent.modules.projects.local_sync as local_sync
    fixture = switch_service
    service = fixture.service
    monkeypatch.setattr(local_sync, "_WINDOWS", True)
    _, binding = await finish_switch(fixture, "daytona")
    remote = fixture.roots[binding.execution.locator]
    (remote / "folder").mkdir()
    (remote / "folder" / "new.txt").write_text("cloud addition")
    (remote / "file.txt").write_text("cloud edit")
    original_capture = service.transfer.capture
    async def capture(ref, *args):
        manifest = await original_capture(ref, *args)
        if ref.backend != "local":
            for entry in manifest["entries"].values():
                entry["mode"] = 0o755 if entry["type"] == "directory" else 0o644
        return manifest
    monkeypatch.setattr(service.transfer, "capture", capture)
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert not preview["conflicts"]
    result = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert result["applied"]
    assert (fixture.root / "file.txt").read_text() == "cloud edit"
    assert (fixture.root / "folder" / "new.txt").read_text() == "cloud addition"
    workspace = (await service.rows(fixture.project["id"]))[1]
    assert workspace.local_sync_revision == result["revision"]
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert not preview["changes"] and not preview["conflicts"]


@pytest.mark.asyncio
async def test_local_sync_preview_recovers_interrupted_type_replacement(switch_service, monkeypatch):
    import agent.modules.projects.local_sync as local_sync
    fixture = switch_service
    service = fixture.service
    _, binding = await finish_switch(fixture, "daytona")
    remote = fixture.roots[binding.execution.locator]
    (remote / "file.txt").unlink()
    (remote / "file.txt").mkdir()
    (remote / "file.txt" / "child.txt").write_text("cloud child")
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    journal = service.runtime_root / str(binding.project_workspace_id) / "local-sync.json"
    original_rename = Path.rename
    def fail_publish(path, destination):
        if path.name == "replacement":
            raise OSError("disk full")
        return original_rename(path, destination)
    def interrupted_cleanup(*args):
        raise RuntimeError("application interrupted")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", fail_publish)
        patch.setattr(local_sync, "_settle_replacement", interrupted_cleanup)
        with pytest.raises(RuntimeError, match="application interrupted"):
            await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert journal.exists() and not (fixture.root / "file.txt").exists()
    service.local_sync = local_sync.WorkspaceLocalSync(service)
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert not preview["conflicts"]
    assert (fixture.root / "file.txt").read_text() == "original"
    assert not journal.exists()
    await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert (fixture.root / "file.txt" / "child.txt").read_text() == "cloud child"
    assert not journal.exists()


@pytest.mark.asyncio
async def test_local_sync_retry_after_partial_write_and_source_changes(switch_service, monkeypatch):
    import agent.modules.projects.local_sync as local_sync
    fixture = switch_service
    service = fixture.service
    _, binding = await finish_switch(fixture, "daytona")
    remote = fixture.roots[binding.execution.locator]
    (remote / "a.txt").write_text("first")
    (remote / "b.txt").write_text("second")
    (remote / "file.txt").unlink()
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    original_apply = local_sync._apply
    def fail(root, stage, desired, current, signatures, changes, **kwargs):
        original_apply(root, stage, desired, current, signatures, [change for change in changes if change["path"] == "a.txt"], **kwargs)
        raise OSError("disk full")
    monkeypatch.setattr(local_sync, "_apply", fail)
    with pytest.raises(OSError, match="disk full"):
        await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert (fixture.root / "a.txt").read_text() == "first"
    assert not service.switcher.switching
    monkeypatch.setattr(local_sync, "_apply", original_apply)
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    assert "a.txt" not in {change["path"] for change in preview["changes"]}
    (remote / "b.txt").write_text("newer")
    with pytest.raises(ProjectWorkspaceError) as failure:
        await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert failure.value.code == "stale_content"
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"])
    assert (fixture.root / "b.txt").read_text() == "newer"
    assert not (fixture.root / "file.txt").exists()


@pytest.mark.asyncio
async def test_local_sync_keeps_writer_reservation_after_client_disconnect(switch_service, monkeypatch):
    import agent.modules.projects.local_sync as local_sync
    fixture = switch_service
    service = fixture.service
    _, binding = await finish_switch(fixture, "daytona")
    (fixture.roots[binding.execution.locator] / "file.txt").write_text("new content")
    preview = await service.local_sync.synchronize(fixture.project["id"], binding.runtime_generation)
    started, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    original_apply = local_sync._apply
    def paused(*args, **kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(10)
        original_apply(*args, **kwargs)
    monkeypatch.setattr(local_sync, "_apply", paused)
    request = asyncio.create_task(service.local_sync.synchronize(
        fixture.project["id"], binding.runtime_generation, preview_token=preview["preview_token"]))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        accepted = service.switcher.tasks[binding.project_workspace_id]
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        with pytest.raises(ProjectWorkspaceError) as failure:
            with service.switcher.run_lease(binding.project_workspace_id):
                pytest.fail("A new run must wait until local writes have settled")
        assert failure.value.code == "workspace_switching"
        release.set()
        await asyncio.wait_for(accepted, timeout=10)
        assert (fixture.root / "file.txt").read_text() == "new content"
        assert not service.switcher.switching and not service.switcher.tasks
    finally:
        release.set()


@pytest.mark.asyncio
async def test_switch_preserves_the_fixed_host_directory(switch_service):
    from agent.delivery.http.dashboard.routes.workspace import WorkspaceResolveBody, resolve_dashboard_workspace
    fixture = switch_service
    await finish_switch(fixture, "daytona")
    _, binding = await finish_switch(fixture, "local")
    old_runtime = Path(binding.execution.locator)
    (old_runtime / "uncommitted.txt").write_text("keep this change")
    _, target = await finish_switch(fixture, "modal")
    assert old_runtime == fixture.root and old_runtime.exists()
    assert (fixture.root / "file.txt").read_text() == "original"
    assert (fixture.roots[target.execution.locator] / "uncommitted.txt").read_text() == "keep this change"
    retired = await fixture.service.find_binding(binding.execution)
    assert retired.execution == target.execution
    assert retired.project_id == fixture.project["id"]
    resolved = await resolve_dashboard_workspace(None, WorkspaceResolveBody(kind="local", locator=str(old_runtime)))
    assert resolved["workspace"]["project_id"] == fixture.project["id"]
    assert resolved["workspace"]["execution"]["backend"] == "modal"
    assert old_runtime.exists()


@pytest.mark.asyncio
async def test_resolving_known_repository_reuses_main_workspace_before_cloud_allocation(switch_service):
    from agent.delivery.http.dashboard.routes.workspace import WorkspaceResolveBody, resolve_dashboard_workspace
    fixture = switch_service
    service = fixture.service
    project, workspace, runtime = await service.rows(fixture.project["id"])
    ref = WorkspaceRef.model_validate_json(runtime.execution_json)
    ref.metadata.update(source="github", repository_id=123, repository_full_name="owner/repo")
    await service._publish_runtime(project, workspace, ref, runtime.generation, "ready")
    _, binding = await finish_switch(fixture, "daytona")
    assert await service.find_repository_binding(999) is None
    count = len(fixture.created)
    resolved = await resolve_dashboard_workspace(None, WorkspaceResolveBody(kind="github", backend="modal", repository_id=123))
    assert resolved["reused_project"] is True
    assert resolved["workspace"]["project_id"] == fixture.project["id"]
    assert resolved["workspace"]["project_workspace_id"] == binding.project_workspace_id
    assert resolved["workspace"]["execution"]["backend"] == "daytona"
    assert len(fixture.created) == count


@pytest.mark.asyncio
async def test_resolving_project_registered_by_repository_key_accepts_scope_metadata(switch_service):
    from fastapi import HTTPException
    from agent.delivery.http.dashboard.routes.workspace import WorkspaceResolveBody, resolve_dashboard_workspace
    fixture = switch_service
    service = fixture.service
    project, workspace, runtime = await service.rows(fixture.project["id"])
    # Only the project scope declares the repository; the execution ref has no repository id.
    ref = WorkspaceRef.model_validate_json(runtime.execution_json)
    ref.metadata.update(source="github")
    await service._publish_runtime(project, workspace, ref, runtime.generation, "ready")
    async with fixture.maker() as session:
        record = await session.get(Project, fixture.project["id"])
        payload = json.loads(record.workspace_json)
        payload["scope"]["metadata"] = {"repository_id": 123}
        record.key = "github:123"
        record.workspace_json = json.dumps(payload)
        await session.commit()
    count = len(fixture.created)
    resolved = await resolve_dashboard_workspace(None, WorkspaceResolveBody(kind="github", backend="modal", repository_id=123))
    assert resolved["reused_project"] is True
    assert resolved["workspace"]["project_id"] == fixture.project["id"]
    assert len(fixture.created) == count
    with pytest.raises(HTTPException) as failure:
        await resolve_dashboard_workspace(None, WorkspaceResolveBody(
            kind="local", locator=str(fixture.root), repository_id=999))
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == "workspace_conflict"
    async with fixture.maker() as session:
        record = await session.get(Project, fixture.project["id"])
        payload = json.loads(record.workspace_json)
        payload["scope"]["metadata"] = {}
        record.workspace_json = json.dumps(payload)
        await session.commit()
    resolved = await resolve_dashboard_workspace(None, WorkspaceResolveBody(
        kind="local", locator=str(fixture.root), repository_id=999))
    assert resolved["workspace"]["project_id"] == fixture.project["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source,target", [(a, b) for a in ("local", "daytona", "modal") for b in ("local", "daytona", "modal") if a != b])
async def test_runtime_switch_preserves_shared_identity_and_all_files(switch_service, monkeypatch, source, target):
    fixture = switch_service
    service, root = fixture.service, fixture.root
    (root / "binary.dat").write_bytes(bytes(range(256)) * 11)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".k41-agent" / "uploads").mkdir(parents=True)
    (root / ".k41-agent" / "uploads" / "input.dat").write_bytes(b"attachment")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "cached").write_text("excluded")
    repository_metadata = {"source": "github", "repository_id": 123, "repository_full_name": "owner/repo",
                           "installation_id": 456, "default_branch": "main", "branch": "feature", "base_branch": "main"}
    project, workspace, runtime = await service.rows(fixture.project["id"])
    if source == "local":
        ref = WorkspaceRef.model_validate_json(runtime.execution_json)
        generation = runtime.generation
    else:
        fixture.roots["source"] = root
        ref = WorkspaceRef(backend=source, locator="source", metadata={"root": "/workspace"})
        generation = runtime.generation + 1
    ref.metadata.update(repository_metadata, repository_path=str(root) if source == "local" else "owner/repo", status="source-status")
    await service._publish_runtime(project, workspace, ref, generation, "ready")
    before = await service.binding(fixture.project["id"])
    from agent.modules.workspaces import repository as mappings
    monkeypatch.setattr(mappings, "get_async_session", catalog.get_async_session)
    for thread in ("first", "second"):
        await mappings.ThreadWorkspaceRepository().upsert(thread_id=thread, workspace=before)
    result, after = await finish_switch(fixture, target)
    destination = Path(after.execution.locator) if target == "local" else fixture.roots[after.execution.locator]
    assert result["operation_id"]
    assert after.project_id == before.project_id
    assert after.project_workspace_id == before.project_workspace_id
    assert after.scope == before.scope
    assert after.runtime_generation == before.runtime_generation + 1
    assert after.execution.backend == target
    assert all(after.execution.metadata.get(key) == value for key, value in repository_metadata.items())
    assert after.execution.metadata["repository_path"] == (after.execution.locator if target == "local" else ".")
    assert after.execution.metadata.get("status") != "source-status"
    if target != "local":
        assert after.execution.metadata["root"] == "/workspace"
    from agent.modules.tools.builtin.github.tools import _github_repo_context
    assert _github_repo_context(SimpleNamespace(context={"workspace": after.model_dump()})) == ("owner/repo", 456)
    assert (destination / "binary.dat").read_bytes() == (root / "binary.dat").read_bytes()
    assert (destination / ".git" / "HEAD").read_bytes() == (root / ".git" / "HEAD").read_bytes()
    assert (destination / ".k41-agent" / "uploads" / "input.dat").read_bytes() == b"attachment"
    assert (destination / "node_modules").exists() is (target == "local")
    assert (root / "file.txt").read_text() == "original"
    assert await service.resolve_thread("first") == await service.resolve_thread("second") == after
    for thread in ("first", "second"):
        mapping = await mappings.ThreadWorkspaceRepository().get(thread)
        assert mapping["workspace"]["execution"]["backend"] == target
    description = await service.describe(fixture.project["id"])
    assert description["runtime"]["transition"]["phase"] == "completed"
    assert description["persistence"]["status"] == "clean"
    if source != "local":
        assert fixture.retired[-1].locator == "source"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["save", "provision", "restore", "verify", "activate"])
async def test_runtime_switch_failures_retain_source(switch_service, monkeypatch, failure):
    import agent.modules.workspaces as workspace_api
    fixture, service = switch_service, switch_service.service
    before = await service.binding(fixture.project["id"])
    failing = AsyncMock(side_effect=OSError("injected " + failure))
    if failure == "save":
        monkeypatch.setattr(service.storage, "put_manifest", lambda *args: (_ for _ in ()).throw(OSError("injected save")))
    elif failure == "provision":
        async def partially_create(*args, **kwargs):
            await fixture.create(*args, **kwargs)
            raise OSError("injected provision")
        monkeypatch.setattr(workspace_api, "create_workspace_backend", partially_create)
    elif failure in {"restore", "verify"}:
        monkeypatch.setattr(service.transfer, failure, failing)
    else:
        monkeypatch.setattr(service, "_publish_runtime", failing)
    await finish_switch(fixture, "daytona")
    assert await service.binding(fixture.project["id"]) == before
    transition = (await service.describe(fixture.project["id"]))["runtime"]["transition"]
    assert transition["phase"] == "failed" and failure in transition["error"]
    assert not service.switcher.switching
    assert (fixture.root / "file.txt").read_text() == "original"
    if fixture.created:
        assert fixture.retired[-1].backend == fixture.created[-1].backend
        assert fixture.retired[-1].locator == fixture.created[-1].locator


@pytest.mark.asyncio
async def test_runtime_switch_blocks_runs_mutations_and_simultaneous_switches(switch_service, monkeypatch):
    from agent.modules.agent_runtime.workspace_admission import workspace_run
    fixture, service = switch_service, switch_service.service
    before = await service.binding(fixture.project["id"])
    restoring, release = asyncio.Event(), asyncio.Event()
    original = service.transfer.restore
    async def restore(*args):
        restoring.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(service.transfer, "restore", restore)
    await service.switcher.request(before.project_id, "modal", before.runtime_generation)
    await restoring.wait()
    try:
        with pytest.raises(ProjectWorkspaceError) as error:
            await service.switcher.request(before.project_id, "daytona", before.runtime_generation)
        assert error.value.code == "workspace_switching"
        with pytest.raises(ProjectWorkspaceError):
            async with workspace_run({"workspace": before.model_dump(), "thread_id": "new-conversation"}):
                pytest.fail("A run entered a switching workspace")
        with pytest.raises(ProjectWorkspaceError):
            async with service.mutation(before.project_id, before.project_workspace_id):
                pytest.fail("A mutation entered a switching workspace")
    finally:
        release.set()
        await service.switcher.close()
    assert (await service.binding(before.project_id)).execution.backend == "modal"
    with pytest.raises(ProjectWorkspaceError) as error:
        await service.switcher.request(before.project_id, "local", before.runtime_generation)
    assert error.value.code == "stale_runtime"


@pytest.mark.asyncio
async def test_runtime_switch_rejects_running_leases_processes_and_pending_approvals(switch_service, monkeypatch):
    fixture, service = switch_service, switch_service.service
    before = await service.binding(fixture.project["id"])
    with service.switcher.run_lease(before.project_workspace_id):
        with pytest.raises(ProjectWorkspaceError):
            await service.switcher.request(before.project_id, "daytona", before.runtime_generation)
    service.running_processes.add((before.project_workspace_id, "process"))
    with pytest.raises(ProjectWorkspaceError, match="processes"):
        await service.switcher.request(before.project_id, "daytona", before.runtime_generation)
    service.running_processes.clear()
    monkeypatch.setattr(service.switcher, "_assert_no_interrupts", AsyncMock(side_effect=ProjectWorkspaceError("Pending approval", "workspace_busy")))
    with pytest.raises(ProjectWorkspaceError, match="approval"):
        await service.switcher.request(before.project_id, "daytona", before.runtime_generation)
    assert not service.switcher.switching and not service.switcher.reservations
    assert await service.binding(before.project_id) == before


@pytest.mark.asyncio
async def test_runtime_switch_checks_paused_child_conversations(switch_service, monkeypatch):
    from agent.modules.projects.runtime_switch import WorkspaceRuntimeSwitcher
    import agent.modules.workflows as workflows
    fixture, service = switch_service, switch_service.service
    before = await service.binding(fixture.project["id"])
    async with fixture.maker() as session:
        session.add(ThreadWorkspace(thread_id="parent", project_id=before.project_id, project_workspace_id=before.project_workspace_id,
                                    working_dir=str(fixture.root)))
        session.add(ConversationThread(thread_id="parent:sub:child"))
        await session.commit()
    threads = await service.switcher._threads(before.project_workspace_id)
    assert threads == {"parent", "parent:sub:child"}
    async def state(config):
        paused = config["configurable"]["thread_id"] == "parent:sub:child"
        return SimpleNamespace(next=(), tasks=[SimpleNamespace(interrupts=[object()])] if paused else [])
    monkeypatch.setattr(workflows, "get_workflow_graph", lambda name: SimpleNamespace(aget_state=state))
    with pytest.raises(ProjectWorkspaceError, match="pending"):
        await WorkspaceRuntimeSwitcher._assert_no_interrupts(service.switcher, threads)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["local", "daytona", "modal"])
async def test_runtime_switch_noop_does_not_reload_files(switch_service, monkeypatch, backend):
    fixture, service = switch_service, switch_service.service
    if backend != "local":
        await finish_switch(fixture, backend)
    monkeypatch.setattr(service.transfer, "capture", AsyncMock(side_effect=AssertionError("Unexpected capture")))
    monkeypatch.setattr(service.switcher, "_assert_no_interrupts", AsyncMock(side_effect=AssertionError("Unexpected workflow lookup")))
    monkeypatch.setattr(service.switcher, "_assert_no_processes", AsyncMock(side_effect=AssertionError("Unexpected process lookup")))
    before = await service.binding(fixture.project["id"])
    result, after = await finish_switch(fixture, backend)
    assert result["operation_id"] is None and before == after


@pytest.mark.asyncio
async def test_runtime_switch_acknowledgement_failure_does_not_delete_active_target(switch_service, monkeypatch):
    fixture, service = switch_service, switch_service.service
    publish = service._publish_runtime
    async def lost_ack(*args, **kwargs):
        await publish(*args, **kwargs)
        raise OSError("Publication acknowledgement lost")
    monkeypatch.setattr(service, "_publish_runtime", lost_ack)
    _, after = await finish_switch(fixture, "daytona")
    assert after.execution.backend == "daytona"
    assert not fixture.retired
    assert (await service.describe(after.project_id))["runtime"]["transition"]["phase"] == "completed"


@pytest.mark.asyncio
async def test_runtime_switch_serves_new_runs_while_retiring_source(switch_service, monkeypatch):
    import agent.modules.workspaces as workspace_api
    fixture, service = switch_service, switch_service.service
    fixture.roots["source"] = fixture.root
    project, workspace, runtime = await service.rows(fixture.project["id"])
    await service._publish_runtime(project, workspace, WorkspaceRef(backend="daytona", locator="source", metadata={"root": "/workspace"}),
                                   runtime.generation + 1, "ready")
    retiring, release = asyncio.Event(), asyncio.Event()
    async def retire(*args, **kwargs):
        retiring.set()
        await release.wait()
    monkeypatch.setattr(workspace_api, "call_workspace_backend_loader", retire)
    before = await service.binding(project.id)
    await service.switcher.request(project.id, "modal", before.runtime_generation)
    await retiring.wait()
    try:
        with service.switcher.run_lease(workspace.id):
            after = await service.ensure(project.id)
            assert after.execution.backend == "modal"
    finally:
        release.set()
        await service.switcher.close()


@pytest.mark.asyncio
async def test_runtime_switch_retries_cleanup_without_retiring_a_shared_sandbox(switch_service, monkeypatch):
    import agent.modules.workspaces as workspace_api
    from agent.modules.projects.workspaces import register_workspace
    fixture, service = switch_service, switch_service.service
    fixture.roots["source"] = fixture.root
    project, workspace, runtime = await service.rows(fixture.project["id"])
    source = WorkspaceRef(backend="daytona", locator="source", metadata={"root": "/workspace"})
    await service._publish_runtime(project, workspace, source, runtime.generation + 1, "ready")
    async with fixture.maker() as session:
        current = await session.get(Project, project.id)
        task = await register_workspace(session, current, source.model_copy(update={"metadata": {"root": "/other"}}), kind="github_task")
        await session.commit()
    await finish_switch(fixture, "modal")
    assert not fixture.retired
    await service.release_task_runtime(task)
    cleanup = AsyncMock(side_effect=[OSError("Provider offline"), None])
    monkeypatch.setattr(workspace_api, "call_workspace_backend_loader", cleanup)
    await service.switcher.recover()
    assert "Provider offline" in (await service.describe(project.id))["runtime"]["transition"]["cleanup_error"]
    await service.switcher.recover()
    assert cleanup.await_count == 2
    assert (await service.describe(project.id))["runtime"]["transition"]["cleanup_error"] == ""
    assert (await service.binding(project.id)).execution.backend == "modal"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["saving", "provisioning", "restoring", "verifying", "activating"])
async def test_runtime_switch_restart_preserves_legacy_local_destination_without_replaying(switch_service, monkeypatch, phase):
    from agent.modules.projects.runtime_switch import WorkspaceRuntimeSwitcher
    fixture, service = switch_service, switch_service.service
    before = await service.binding(fixture.project["id"])
    destination = service.runtime_root / str(before.project_workspace_id) / "switch-interrupted"
    destination.mkdir(parents=True)
    (destination / "partial").write_bytes(b"partial")
    state = {"operation_id": "interrupted", "project_id": before.project_id, "phase": phase,
             "source": before.execution.model_dump(), "source_generation": before.runtime_generation,
             "target_backend": "local", "target": WorkspaceRef(locator=str(destination)).model_dump(),
             "revision": None, "previous_pin": None, "cleanup": []}
    await service.switcher._write(before.project_workspace_id, state)
    service.switcher = WorkspaceRuntimeSwitcher(service)
    await service.switcher.recover()
    assert (destination / "partial").read_bytes() == b"partial"
    assert await service.binding(before.project_id) == before
    assert (await service.describe(before.project_id))["runtime"]["transition"]["phase"] == "failed"
    assert not fixture.created
