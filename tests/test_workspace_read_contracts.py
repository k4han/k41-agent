"""Source selection, generation fencing, and consistent workspace paths."""

from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.modules.projects.backend import DeferredWorkspaceBackend, ThreadWorkspaceBackend
from agent.modules.projects.workspaces import ProjectWorkspaceError
from agent.modules.workspaces import WorkspaceRef, WorkspaceUnavailableError
from agent.modules.workspaces.posix_utils import resolve_remote_path
from agent.modules.workspaces.runtime import runtime_observation


@pytest.mark.parametrize("path", ["../outside", "src/../../outside", "/other/file", "/workspace-other/file",
                                 "C:/project/file", "C:file", r"\\server\share\file", "//server/share/file", "a\x00b"])
def test_workspace_paths_reject_escape_and_foreign_paths(path):
    with pytest.raises(ValueError):
        resolve_remote_path("/workspace", path)


@pytest.mark.parametrize("path,expected", [("", "/workspace"), (".", "/workspace"),
    ("src/main.py", "/workspace/src/main.py"), ("/workspace/src/main.py", "/workspace/src/main.py"),
    (r"src\main.py", "/workspace/src/main.py")])
def test_workspace_paths_preserve_contained_legacy_paths(path, expected):
    assert resolve_remote_path("/workspace", path) == expected


@pytest.fixture(params=["project", "thread"])
def reader(request, monkeypatch, tmp_path):
    import agent.modules.workspaces as workspace_api
    ref = WorkspaceRef(backend="modal", locator="live", metadata={"root": "/workspace"})
    runtime = SimpleNamespace(generation=7, status="ready")
    saved_reads = []
    @asynccontextmanager
    async def saved_view(*args):
        saved_reads.append(True)
        yield WorkspaceRef(locator=str(tmp_path), metadata={"saved_revision": "saved-revision", "last_saved_at": "saved-time"})
    live = SimpleNamespace(file=AsyncMock(return_value={"content": "new"}), read_bytes=AsyncMock(return_value=b"new"),
                           read_text=AsyncMock(return_value="new"))
    saved = SimpleNamespace(file=AsyncMock(return_value={"content": "old"}), read_bytes=AsyncMock(return_value=b"old"),
                            read_text=AsyncMock(return_value="old"))
    async def factory(target, **kwargs):
        return live if target.backend == "modal" else saved
    monkeypatch.setattr(workspace_api, "create_workspace_runtime_backend", factory)
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_is_running", AsyncMock(return_value=True))
    if request.param == "project":
        binding = SimpleNamespace(execution=ref, project_id=1, project_workspace_id=1, runtime_generation=7)
        service = SimpleNamespace(rows=AsyncMock(return_value=(None, None, runtime)), saved_view=saved_view)
        facade = DeferredWorkspaceBackend(binding, service)
    else:
        row = SimpleNamespace(host_mirror_json=json.dumps({"generation": "thread-generation"}))
        mirrors = SimpleNamespace(thread_status=AsyncMock(return_value=([row], None)), thread_ref=lambda row: ref,
                                  saved_thread_view=saved_view)
        facade = ThreadWorkspaceBackend(ref, "thread", SimpleNamespace(host_mirrors=mirrors))
    return SimpleNamespace(facade=facade, live=live, saved=saved, saved_reads=saved_reads, runtime=runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [FileNotFoundError("deleted"), PermissionError("denied"), ValueError("invalid")])
async def test_authoritative_live_errors_never_fall_back(reader, error):
    reader.live.file.side_effect = error
    with pytest.raises(type(error)):
        await reader.facade.file("deleted.txt")
    assert not reader.saved_reads


@pytest.mark.asyncio
async def test_binary_saved_read_always_reports_its_source(reader):
    reader.live.read_bytes.side_effect = ConnectionError("provider offline")
    result = await reader.facade.read_result("read_bytes", "/workspace/file.bin", consistency="saved_ok")
    assert result.data == b"old" and result.data_source == "saved"
    assert result.saved_revision == "saved-revision" and result.fallback_reason == "provider_unavailable"
    assert result.headers()["X-Workspace-Saved-Revision"] == "saved-revision"


@pytest.mark.asyncio
async def test_raw_reads_require_live_data_unless_explicitly_requested(reader):
    reader.live.read_text.side_effect = WorkspaceUnavailableError("runtime gone")
    with pytest.raises(WorkspaceUnavailableError):
        await reader.facade.read_text("file.txt")
    assert not reader.saved_reads
    result = await reader.facade.read_result("read_text", "file.txt", consistency="saved_ok")
    assert result.data == "old" and result.saved_revision == "saved-revision"


@pytest.mark.asyncio
async def test_live_result_does_not_claim_a_saved_revision(reader):
    result = await reader.facade.read_result("file", "file.txt")
    assert result.data == {"content": "new"}
    assert result.saved_revision is None and not result.metadata()["stale"]
    assert not reader.saved_reads


@pytest.mark.asyncio
async def test_paths_are_validated_even_when_provider_is_down(reader, monkeypatch):
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_is_running", AsyncMock(return_value=False))
    with pytest.raises(ValueError):
        await reader.facade.file("C:/host/file")
    assert not reader.saved_reads


@pytest.mark.parametrize("backend,state,resume,replace", [
    ("daytona", "stopped", True, False), ("daytona", "archived", True, False),
    ("modal", "stopped", False, True), ("modal", "finished", False, True),
    ("daytona", "unknown", False, False), ("modal", "unknown", False, False),
])
def test_provider_observations_distinguish_resume_from_replacement(backend, state, resume, replace):
    observed = runtime_observation(backend, state)
    assert observed.can_resume is resume and observed.needs_replacement is replace


def test_legacy_saved_files_remain_visible_without_a_materialized_host_mirror():
    from agent.modules.workspaces.host_mirror import mirror_summary
    row = SimpleNamespace(sync_policy="mirror_strict", host_mirror_json=json.dumps({
        "remote_revision": "saved", "status": "pending", "backend_status": "stopped",
    }))
    summary = mirror_summary(row)
    assert summary["remote_revision"] == "saved" and summary["path"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["daytona", "modal"])
async def test_sdk_missing_files_are_not_treated_as_missing_runtimes(monkeypatch, backend):
    from agent.modules.workspaces.reads import provider_read_error
    if backend == "daytona":
        errors = pytest.importorskip("daytona.common.errors")
        missing, denied = errors.DaytonaNotFoundError("missing"), errors.DaytonaAuthorizationError("denied")
    else:
        errors = pytest.importorskip("modal.exception")
        missing, denied = errors.NotFoundError("missing"), errors.PermissionDeniedError("denied")
    ref = WorkspaceRef(backend=backend, locator="runtime")
    probe = AsyncMock(return_value="started")
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_runtime_status", probe)
    assert isinstance(await provider_read_error(ref, missing), FileNotFoundError)
    assert isinstance(await provider_read_error(ref, denied), PermissionError)
    probe.return_value = "unknown"
    assert await provider_read_error(ref, missing) is missing
    probe.return_value = "destroyed"
    assert isinstance(await provider_read_error(ref, missing), WorkspaceUnavailableError)


@pytest.mark.asyncio
async def test_allocation_reconciliation_requires_an_exact_operation_tag(monkeypatch):
    import agent.modules.workspaces as workspaces
    from agent.modules.workspaces.transitions import RuntimeTransitionCoordinator
    records = [{"sandbox_id": "unrelated", "metadata": {}},
               {"sandbox_id": "target", "root": "/workspace", "metadata": {"k41_operation_id": "operation"}}]
    inventory = AsyncMock(return_value=records)
    registry = SimpleNamespace(require=lambda backend: SimpleNamespace(inventory_loader="inventory"))
    monkeypatch.setattr(workspaces, "get_workspace_backend_registry", lambda: registry)
    monkeypatch.setattr(workspaces, "call_workspace_backend_loader", inventory)
    assert await RuntimeTransitionCoordinator.find_allocation("modal", None) is None
    inventory.assert_not_awaited()
    target = await RuntimeTransitionCoordinator.find_allocation("modal", "operation")
    assert target.locator == "target"
    records.append({"sandbox_id": "duplicate", "metadata": {"k41_operation_id": "operation"}})
    assert await RuntimeTransitionCoordinator.find_allocation("modal", "operation") is None


def test_allocation_uncertainty_includes_provider_server_failures():
    from agent.modules.workspaces.transitions import RuntimeTransitionCoordinator
    errors = pytest.importorskip("daytona.common.errors")
    assert RuntimeTransitionCoordinator.allocation_uncertain(errors.DaytonaTimeoutError("timeout"))
    assert RuntimeTransitionCoordinator.allocation_uncertain(errors.DaytonaError("server", status_code=503))
    assert not RuntimeTransitionCoordinator.allocation_uncertain(errors.DaytonaValidationError("invalid", status_code=400))


@pytest.mark.asyncio
async def test_managed_local_reads_reject_changed_generations():
    from agent.modules.projects.backend import ManagedWorkspaceBackend
    runtime = SimpleNamespace(generation=1)
    service = SimpleNamespace(rows=AsyncMock(return_value=(None, None, runtime)))
    binding = SimpleNamespace(project_id=1, project_workspace_id=1, runtime_generation=1)
    async def read(path):
        runtime.generation = 2
        return b"stale"
    facade = ManagedWorkspaceBackend(SimpleNamespace(read_bytes=read), binding, service)
    with pytest.raises(ProjectWorkspaceError) as changed:
        await facade.read_result("read_bytes", "file.bin")
    assert changed.value.code == "stale_runtime"


@pytest.mark.asyncio
async def test_generation_changes_during_live_reads_are_rejected(monkeypatch, tmp_path):
    import agent.modules.workspaces as workspaces
    ref = WorkspaceRef(backend="modal", locator="current", metadata={"root": "/workspace"})
    runtime = SimpleNamespace(generation=1, status="ready")
    binding = SimpleNamespace(execution=ref, project_id=1, project_workspace_id=1, runtime_generation=1)
    service = SimpleNamespace(rows=AsyncMock(return_value=(None, None, runtime)))
    facade = DeferredWorkspaceBackend(binding, service)
    async def read(path):
        runtime.generation = 2
        return "old-generation-content"
    monkeypatch.setattr(workspaces, "create_workspace_runtime_backend", AsyncMock(return_value=SimpleNamespace(read_text=read)))
    monkeypatch.setattr("agent.modules.workspaces.host_mirror.workspace_is_running", AsyncMock(return_value=True))
    with pytest.raises(ProjectWorkspaceError) as changed:
        await facade.read_result("read_text", "file.txt")
    assert changed.value.code == "stale_runtime"
