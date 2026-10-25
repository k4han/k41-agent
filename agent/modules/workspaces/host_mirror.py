"""Application-owned host mirrors independent of checkpoint durability."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, asynccontextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from agent.modules.projects.local_sync import (
    _apply, _capture, _plan, _recover_replacement, _run_writer, _same_entry, _sweep_temporary, _verify,
)
from agent.modules.projects.models import ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.storage import LocalProjectStorage
from agent.modules.projects.workspaces import ProjectWorkspaceError, _session, source_key, storage_timestamp
from agent.modules.workspaces.models import ThreadWorkspace
from agent.modules.workspaces.refs import WorkspaceRef, workspace_ref_from_columns
from agent.shared.infrastructure.db.base import utcnow

logger = logging.getLogger(__name__)
EMPTY = {"version": 1, "entries": {}}


def mirror_state(row) -> dict:
    return json.loads(getattr(row, "host_mirror_json", None) or "{}")


def mirror_summary(row) -> dict | None:
    state = mirror_state(row)
    return {name: state.get(name) for name in (
        "path", "status", "revision", "remote_revision", "generation", "last_synced_at",
        "last_error", "conflicts", "resolution_token",
        "backend_status",
    )} if state.get("path") else None


def thread_storage_key(rows) -> str:
    return next((mirror_state(row)["storage_key"] for row in rows if mirror_state(row).get("storage_key")),
                str(min(row.id for row in rows)))


async def workspace_runtime_status(ref: WorkspaceRef) -> str:
    """Probe providers without attaching, starting or recreating a sandbox."""
    if ref.backend == "local":
        return "started"
    if ref.backend == "daytona":
        from agent.modules.workspaces.daytona_backend import (
            get_daytona_sandbox, _is_daytona_not_found_error, _refresh_sandbox_state,
        )
        def probe():
            return _refresh_sandbox_state(get_daytona_sandbox(ref))
        try:
            return await asyncio.to_thread(probe)
        except Exception as exc:
            return "destroyed" if _is_daytona_not_found_error(exc) else "unknown"
    if ref.backend == "modal":
        from agent.modules.workspaces.modal_backend import probe_modal_sandbox
        try:
            status = await probe_modal_sandbox(ref.locator)
        except Exception:
            return "unknown"
        return status["status"] if status else "unknown"
    return "unknown"


async def workspace_is_running(ref: WorkspaceRef) -> bool:
    return await workspace_runtime_status(ref) == "started"


def validate_host_names(manifest: dict) -> None:
    if os.name != "nt":
        return
    seen = set()
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for name in manifest["entries"]:
        key = name.casefold()
        if key in seen or any(part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved
                              or any(char in '<>:"\\|?*' or ord(char) < 32 for char in part)
                              for part in name.split("/")):
            raise ProjectWorkspaceError("A sandbox path cannot be represented safely on this host: " + name,
                                        "host_path_unsupported")
        seen.add(key)


def comparison(base, desired, current, root):
    changes, conflicts = _plan(base, desired, current, root)
    # Host-only changes must not silently become part of the next baseline.
    conflicts = set(conflicts)
    for name in base["entries"].keys() | current["entries"].keys():
        actual = current["entries"].get(name)
        if not _same_entry(actual, base["entries"].get(name)) and not _same_entry(actual, desired["entries"].get(name)):
            conflicts.add(name)
    return changes, sorted(conflicts)


def cloud_entry(entry, previous):
    """Windows host edits cannot express changes to POSIX execute or directory bits."""
    if os.name != "nt" or entry["type"] == "symlink":
        return entry
    previous = previous if previous and previous["type"] == entry["type"] else None
    if entry["type"] == "directory":
        return dict(entry, mode=previous["mode"] if previous else 0o755)
    mode = previous["mode"] if previous else 0o644
    mode = mode | 0o200 if entry["mode"] & 0o200 else mode & ~0o222
    return dict(entry, mode=mode)


def resolution_token(generation, revision, base, current):
    return hashlib.sha256(json.dumps([generation, revision, base, current], sort_keys=True).encode()).hexdigest()


class HostMirrorService:
    def __init__(self, service):
        self.service = service
        storage_root = getattr(service.storage, "root", None)
        storage_root = Path(storage_root) if storage_root is not None else service.runtime_root.parent / "project-storage"
        self.temporary_storage = LocalProjectStorage(storage_root / "thread-mirrors")
        self.task = None
        self.jobs = {}
        self.thread_locks = {}
        self.tokens = {}
        self.last_reconciled = {}
        self.failures = {}
        self.transitions = set()

    def journal_path(self, store, key):
        root = getattr(store, "root", None)
        return (Path(root) if root is not None else self.service.runtime_root) / key / "host-sync.json"

    def project_path(self, workspace, runtime) -> Path:
        state = mirror_state(workspace)
        if state.get("path"):
            return Path(state["path"])
        source = WorkspaceRef.model_validate_json(workspace.source_json)
        execution = WorkspaceRef.model_validate_json(runtime.execution_json)
        if source.backend == "local":
            return Path(source.locator)
        if execution.backend == "local":
            # Adopt existing managed runtimes on upgrade without losing their files.
            return Path(execution.locator)
        return self.service.runtime_root / str(workspace.id) / "workspace"

    async def prepare_local_capture(self, workspace, runtime):
        state = mirror_state(workspace)
        revision = state.get("revision") or workspace.local_sync_revision
        if not revision:
            return
        ref = WorkspaceRef.model_validate_json(runtime.execution_json)
        root = Path(ref.locator)
        if root.resolve() != self.project_path(workspace, runtime).resolve():
            return
        manifest = await asyncio.to_thread(self.service.storage.get_manifest, str(workspace.id), revision)
        for name in ("host-sync.json", "local-sync.json"):
            journal = (self.journal_path(self.service.storage, str(workspace.id)) if name == "host-sync.json"
                       else self.service.runtime_root / str(workspace.id) / name)
            await _run_writer(_recover_replacement, root, journal)
        await _run_writer(_sweep_temporary, root, manifest["entries"].keys())

    async def _save_project(self, workspace, runtime, state):
        session = await _session()
        async with session:
            result = await session.execute(update(ProjectWorkspace).where(
                ProjectWorkspace.id == workspace.id,
                select(WorkspaceRuntime.id).where(WorkspaceRuntime.project_workspace_id == workspace.id,
                                                  WorkspaceRuntime.generation == runtime.generation).exists(),
            ).values(host_mirror_json=json.dumps(state), local_sync_revision=state.get("revision")))
            if result.rowcount != 1:
                raise ProjectWorkspaceError("Host sync belongs to an obsolete runtime.", "stale_runtime")
            await session.commit()
        self._notify(project_id=workspace.project_id, project_workspace_id=workspace.id)

    def _notify(self, **identity):
        from agent.modules.agent_runtime import get_active_session_registry
        get_active_session_registry(self.service.container).publish("workspace_files_changed", identity)

    async def after_checkpoint(self, workspace, runtime, revision):
        """Caller holds the project writer lock; mirror errors never invalidate a saved checkpoint."""
        state = mirror_state(workspace)
        state.setdefault("path", str(self.project_path(workspace, runtime)))
        state.setdefault("revision", workspace.local_sync_revision)
        ref = WorkspaceRef.model_validate_json(runtime.execution_json)
        state["backend_status"] = "started"
        try:
            if ref.backend == "local" and Path(ref.locator).resolve() == Path(state["path"]).resolve():
                state.update(status="synced", revision=revision, remote_revision=revision,
                             generation=runtime.generation, conflicts=[], last_error="", resolution_token=None,
                             last_synced_at=storage_timestamp(utcnow()))
            else:
                await self._save_project(workspace, runtime, dict(state, status="syncing", generation=runtime.generation))
                state = await self._apply(self.service.storage, str(workspace.id), state, revision, runtime.generation)
        except Exception as exc:
            if getattr(exc, "code", None) == "stale_runtime":
                raise
            state.update(status="error", generation=runtime.generation, last_error=str(exc))
        await self._save_project(workspace, runtime, state)
        return state

    async def _apply(self, store, key, state, revision, generation, *, force=False, expected_current=None):
        state = dict(state)
        desired = await asyncio.to_thread(store.get_manifest, key, revision)
        validate_host_names(desired)
        root = Path(state["path"])
        if root.is_symlink():
            raise ProjectWorkspaceError("Host workspace root must not be a symlink.", "stale_content")
        root.mkdir(parents=True, exist_ok=True)
        base = await asyncio.to_thread(store.get_manifest, key, state["revision"]) if state.get("revision") else EMPTY
        journal = self.journal_path(store, key)
        await _run_writer(_recover_replacement, root, journal)
        await _run_writer(_sweep_temporary, root, base["entries"].keys() | desired["entries"].keys())
        current, signatures = await asyncio.to_thread(_capture, root)
        if expected_current is not None and current != expected_current:
            raise ProjectWorkspaceError("Host files changed after conflict approval.", "stale_content")
        changes, conflicts = await asyncio.to_thread(comparison, base, desired, current, root)
        token = resolution_token(generation, revision, state.get("revision"), current)
        state.update(remote_revision=revision, generation=generation, conflicts=conflicts,
                     resolution_token=token, last_error="")
        if conflicts and not force:
            state["status"] = "conflict"
            return state
        if force:
            changes, conflicts = await asyncio.to_thread(_plan, current, desired, current, root)
            if conflicts:
                raise ProjectWorkspaceError("Host directory contents changed during resolution.", "stale_content")
            base = current

        def apply():
            with tempfile.TemporaryDirectory(prefix="k41-host-sync-") as stage:
                self.service.transfer.materialize(store, key, revision, Path(stage))
                _apply(root, Path(stage), desired, current, signatures, changes, journal=journal)
                actual, _ = _capture(root)
                _verify(base, desired, actual)
                if comparison(desired, desired, actual, root)[1]:
                    raise ProjectWorkspaceError("Host files changed during synchronization.", "stale_content")
        if changes:
            await _run_writer(apply)
        state.update(status="synced", revision=revision, conflicts=[], resolution_token=None,
                     last_synced_at=storage_timestamp(utcnow()))
        return state

    async def synchronize_project(self, project_id, workspace_id=None, *, generation=None, passive=False):
        _, workspace, runtime = await self.service.rows(project_id, workspace_id)
        if generation is not None and generation != runtime.generation:
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        await self.service.checkpoint(project_id, workspace.id, generation=runtime.generation, passive=passive)
        _, workspace, _ = await self.service.rows(project_id, workspace.id)
        return mirror_summary(workspace)

    async def _resolve(self, store, key, state, ref, generation, action, token, save_state, *, runtime_status=None):
        if ref.backend == "local" and Path(ref.locator).resolve() == Path(state["path"]).resolve():
            raise ProjectWorkspaceError("Local workspaces already write directly to the host.", "invalid_source")
        status = runtime_status if runtime_status is not None else await workspace_runtime_status(ref)
        if ref.backend != "local" and status in {"stopped", "destroyed"}:
            if action != "keep_sandbox":
                raise ProjectWorkspaceError("Recover the sandbox before sending host changes to it.", "runtime_unavailable")
            revision = state.get("remote_revision") or state.get("revision")
            if not revision:
                raise ProjectWorkspaceError("No saved sandbox files are available.", "missing_data")
            manifest = await asyncio.to_thread(store.get_manifest, key, revision)
        else:
            manifest = await self.service.transfer.capture(ref, store, key)
            revision = await asyncio.to_thread(store.put_manifest, key, manifest)
        root = Path(state["path"])
        current, _ = await asyncio.to_thread(_capture, root)
        if not token or resolution_token(generation, revision, state.get("revision"), current) != token:
            raise ProjectWorkspaceError("Host or sandbox files changed. Refresh conflicts before resolving them.", "stale_content")
        base = await asyncio.to_thread(store.get_manifest, key, state["revision"]) if state.get("revision") else EMPTY
        _, conflicts = await asyncio.to_thread(comparison, base, manifest, current, root)
        if not conflicts:
            raise ProjectWorkspaceError("These conflicts have already been resolved.", "stale_content")
        backup = await self.service.transfer.capture(WorkspaceRef(locator=str(root)), store, key)
        if backup != current:
            raise ProjectWorkspaceError("Host files changed while preparing conflict resolution.", "stale_content")
        state = dict(state, host_backup_revision=await asyncio.to_thread(store.put_manifest, key, backup))
        # Preserve approved host bytes durably before any replacement or remote mutation.
        await save_state(state)
        if action == "keep_sandbox":
            return await self._apply(store, key, state, revision, generation, force=True, expected_current=current)
        if action != "keep_host":
            raise ValueError("Unknown host conflict resolution.")
        # A parent conflict owns its subtree. Preserve all unrelated sandbox changes.
        roots = [name for name in conflicts if not any(name.startswith(parent + "/") for parent in conflicts if parent != name)]
        entries = dict(manifest["entries"])
        for name in list(entries):
            if any(name == parent or name.startswith(parent + "/") for parent in roots):
                entries.pop(name)
        for name, entry in current["entries"].items():
            if any(name == parent or name.startswith(parent + "/") for parent in roots):
                entries[name] = cloud_entry(entry, manifest["entries"].get(name))
        desired = dict(manifest, entries=entries)
        await self.service.transfer.reconcile(ref, store, key, manifest, desired)
        actual = await self.service.transfer.capture(ref, store, key)
        revision = await asyncio.to_thread(store.put_manifest, key, actual)
        return await self._apply(store, key, state, revision, generation)

    async def resolve_project(self, project_id, workspace_id, generation, action, token):
        host_only = action == "keep_sandbox"
        async with self.service.mutation(project_id, workspace_id, generation=generation, host_only=host_only):
            _, workspace, runtime = await self.service.rows(project_id, workspace_id)
            ref = WorkspaceRef.model_validate_json(runtime.execution_json)
            state = mirror_state(workspace)
            if not state.get("path"):
                raise ProjectWorkspaceError("Synchronize the host workspace first.", "host_sync_required")
            status = None
            if host_only:
                status = ("destroyed" if ref.backend != "local" and runtime.status == "unavailable"
                          else await workspace_runtime_status(ref))
            saved_only = host_only and ref.backend != "local" and status in {"stopped", "destroyed"}
            try:
                state = await self._resolve(self.service.storage, str(workspace.id), state, ref, generation, action, token,
                                            lambda value: self._save_project(workspace, runtime, value), runtime_status=status)
                await self._save_project(workspace, runtime, state)
            finally:
                if not saved_only:
                    checkpoint = asyncio.create_task(self.service._checkpoint(project_id, workspace_id, generation))
                    try:
                        await asyncio.shield(checkpoint)
                    except asyncio.CancelledError:
                        await checkpoint
                        raise
        _, workspace, _ = await self.service.rows(project_id, workspace_id)
        return mirror_summary(workspace)

    async def synchronize_thread(self, thread_id, *, generation=None, action="retry", token=None):
        rows, _ = await self.thread_status(thread_id)
        row = sorted(rows, key=lambda item: (":sub:" in item.thread_id, item.id))[0]
        ref = self.thread_ref(row)
        current_generation = mirror_state(row).get("generation") or source_key(ref)
        if generation is not None and generation != current_generation:
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        if action == "retry":
            await self._poll_threads(rows, force=True)
        else:
            key = thread_storage_key(rows)
            async with self.thread_locks.setdefault(key, asyncio.Lock()):
                rows = await self._refresh_threads(rows, ref)
                if mirror_state(rows[0]).get("generation", source_key(ref)) != current_generation:
                    raise ProjectWorkspaceError("Thread runtime changed.", "stale_runtime")
                state = await self._resolve(self.temporary_storage, key, mirror_state(rows[0]), ref, current_generation, action, token,
                                            lambda value: self._save_threads(rows, ref, value))
                await self._save_threads(rows, ref, state)
        _, result = await self.thread_status(thread_id)
        return result

    async def _changes(self, ref, identity, *, force=False, checked=False):
        if not checked:
            status = await workspace_runtime_status(ref)
            if status != "started":
                self.tokens.pop(identity, None)
                return {"backend_status": status, "changed": False, "token": None}
        from agent.modules.tools import get_coding_service
        from agent.modules.tools.coding.contracts import InvocationContext
        context = InvocationContext(agent_name="host-mirror", workspace=str(ref.metadata.get("root", "")),
                                    thread_id="", backend=ref.backend, locator=ref.locator)
        remote = get_coding_service(self.service.container).remote
        client = await remote.client(context, passive=True)
        await client.ensure_started()
        return await client.call({"operation": "workspace_changes", "workspace": client.backend.root,
                                  "since": self.tokens.get(identity), "force": force})

    async def _poll_project(self, workspace, runtime):
        identity = ("project", workspace.id, runtime.generation)
        ref = WorkspaceRef.model_validate_json(runtime.execution_json)
        if ref.backend == "local":
            return
        if self.service.lock(workspace.id).locked() or workspace.id in self.service.switcher.switching:
            return
        status = await workspace_runtime_status(ref)
        if status != "started":
            self.tokens.pop(identity, None)
            async with self.service.lock(workspace.id):
                project, current, active = await self.service.rows(workspace.project_id, workspace.id)
                if active.generation != runtime.generation:
                    return
                if status == "destroyed" or (ref.backend == "modal" and status == "stopped"):
                    await self.service._record_provision_decision(project, current, active)
                    self.service.active.discard(workspace.id)
                state = mirror_state(current)
                if state.get("status") not in {"conflict", "error"}:
                    previous = dict(state)
                    state.setdefault("path", str(self.project_path(current, active)))
                    state.setdefault("revision", current.local_sync_revision)
                    state.update(generation=active.generation, conflicts=[])
                    state["backend_status"] = status
                    state.update(status="pending", last_error="Sandbox is stopped or unavailable; showing saved files.")
                    if state != previous:
                        await self._save_project(current, active, state)
            return
        force = time.monotonic() - self.last_reconciled.get(identity, 0) >= 30
        changes = await self._changes(ref, identity, checked=True, force=force)
        if runtime.status == "stopped":
            async with self.service.lock(workspace.id):
                session = await _session()
                async with session:
                    await session.execute(update(WorkspaceRuntime).where(
                        WorkspaceRuntime.project_workspace_id == workspace.id, WorkspaceRuntime.generation == runtime.generation,
                        WorkspaceRuntime.status == "stopped",
                    ).values(status="ready", updated_at=utcnow()))
                    await session.commit()
        if changes and (changes["changed"] or mirror_state(workspace).get("status") in {None, "pending", "error"}):
            state = await self.synchronize_project(workspace.project_id, workspace.id, generation=runtime.generation, passive=True)
            if state["status"] == "error":
                raise ProjectWorkspaceError(state["last_error"], "host_sync_error")
            self.tokens[identity] = changes["token"]
            self.last_reconciled[identity] = time.monotonic()

    async def _thread_rows(self):
        session = await _session()
        async with session:
            rows = (await session.execute(select(ThreadWorkspace).where(ThreadWorkspace.project_workspace_id.is_(None),
                                                                       ThreadWorkspace.execution_locator.is_not(None)))).scalars().all()
        groups = {}
        for row in rows:
            ref = self.thread_ref(row)
            groups.setdefault(source_key(ref), []).append(row)
        for rows in groups.values():
            rows.sort(key=lambda item: (":sub:" in item.thread_id, item.id))
        return groups

    async def _refresh_threads(self, rows, ref):
        session = await _session()
        async with session:
            current = (await session.execute(select(ThreadWorkspace).where(
                ThreadWorkspace.id.in_([row.id for row in rows]), ThreadWorkspace.project_workspace_id.is_(None),
            ))).scalars().all()
        current = [row for row in current if source_key(self.thread_ref(row)) == source_key(ref)]
        if not current:
            raise ProjectWorkspaceError("Thread runtime changed.", "stale_runtime")
        current.sort(key=lambda item: (":sub:" in item.thread_id, item.id))
        return current

    @staticmethod
    def thread_ref(row):
        return workspace_ref_from_columns(backend=row.execution_backend, locator=row.execution_locator,
                                          label=row.execution_label, metadata_json=row.execution_metadata_json)

    async def _save_threads(self, rows, ref, state):
        session = await _session()
        async with session:
            result = await session.execute(update(ThreadWorkspace).where(
                ThreadWorkspace.id.in_([row.id for row in rows]), ThreadWorkspace.project_workspace_id.is_(None),
                ThreadWorkspace.execution_backend == ref.backend, ThreadWorkspace.execution_locator == ref.locator,
                or_(*(and_(ThreadWorkspace.id == row.id, ThreadWorkspace.execution_metadata_json == row.execution_metadata_json)
                      for row in rows)),
            ).values(host_mirror_json=json.dumps(state)))
            if result.rowcount == 0:
                raise ProjectWorkspaceError("Thread workspace changed during synchronization.", "stale_runtime")
            await session.commit()
        self._notify(thread_id=rows[0].thread_id.split(":sub:", 1)[0])

    async def _poll_threads(self, rows, *, force=False):
        row = sorted(rows, key=lambda item: (":sub:" in item.thread_id, item.id))[0]
        ref = self.thread_ref(row)
        key = thread_storage_key(rows)
        identity = ("thread", key)
        lock = self.thread_locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            if force:
                raise ProjectWorkspaceError("Workspace synchronization is already running.", "workspace_busy")
            return
        async with lock:
            rows = await self._refresh_threads(rows, ref)
            row = rows[0]
            state = mirror_state(row)
            state["storage_key"] = key
            generation = state.get("generation") or source_key(ref)
            state = await self._cleanup_threads(rows, ref, state)
            if ref.backend == "local":
                state.update(path=ref.locator, status="synced", generation=generation, conflicts=[], last_error="")
                if state != mirror_state(row):
                    await self._save_threads(rows, ref, state)
                return
            from agent.modules.workspaces.service import temp_workspace_root
            state.setdefault("path", str(temp_workspace_root() / ("cloud-" + source_key(ref)[:32])))
            state.setdefault("generation", generation)
            state.setdefault("status", "pending")
            state.setdefault("conflicts", [])
            if not mirror_state(row).get("path"):
                await self._save_threads(rows, ref, state)
            force = force or time.monotonic() - self.last_reconciled.get(identity, 0) >= 30
            changes = await self._changes(ref, identity, force=force)
            if changes is None or changes.get("backend_status", "started") != "started":
                state["backend_status"] = changes.get("backend_status", "unknown") if changes else "unknown"
                if state.get("path") and state.get("status") not in {"conflict", "error"}:
                    state.update(status="pending", last_error="Sandbox is stopped or unavailable; showing saved files.")
                if state != mirror_state(row):
                    await self._save_threads(rows, ref, state)
                return
            state["backend_status"] = "started"
            if not changes["changed"] and state.get("status") in {"synced", "conflict"}:
                return
            try:
                ref = ref.model_copy(deep=True)
                ref.metadata["_require_running"] = True
                previous_revision = state.get("revision")
                manifest = await self.service.transfer.capture(ref, self.temporary_storage, key)
                revision = await asyncio.to_thread(self.temporary_storage.put_manifest, key, manifest)
                await self._refresh_threads(rows, ref)
                # Keep the durable sandbox capture even if applying it to the host fails.
                state["remote_revision"] = revision
                await self._save_threads(rows, ref, state)
                state = await self._apply(self.temporary_storage, key, state, revision, generation)
                if previous_revision and previous_revision != state.get("revision"):
                    state["previous_revision"] = previous_revision
            except Exception as exc:
                state.update(status="error", last_error=str(exc), generation=generation)
            await self._save_threads(rows, ref, state)
            keep = {state[name] for name in ("revision", "remote_revision", "previous_revision", "host_backup_revision") if state.get(name)}
            if keep:
                await _run_writer(self.temporary_storage.collect, key, keep)
            if state["status"] == "error":
                raise ProjectWorkspaceError(state["last_error"], "host_sync_error")
            self.tokens[identity] = changes["token"]
            self.last_reconciled[identity] = time.monotonic()

    @asynccontextmanager
    async def saved_thread_view(self, thread_id):
        rows, state = await self.thread_status(thread_id)
        if not state or not (state.get("remote_revision") or state.get("revision")):
            raise ProjectWorkspaceError("No saved files are available for this workspace.", "runtime_unavailable")
        key = thread_storage_key(rows)
        async with self.thread_locks.setdefault(key, asyncio.Lock()):
            with tempfile.TemporaryDirectory(prefix="k41-thread-view-") as path:
                revision = state.get("remote_revision") or state["revision"]
                await _run_writer(self.service.transfer.materialize, self.temporary_storage, key, revision, Path(path))
                yield WorkspaceRef(locator=path, metadata={"saved_revision": revision, "last_saved_at": state.get("last_synced_at")})

    async def switch_thread(self, thread_id, backend, generation):
        if self.service.closing:
            raise ProjectWorkspaceError("The application is shutting down.", "workspace_closing")
        task = asyncio.create_task(self._switch_thread(thread_id, backend, generation))
        self.transitions.add(task)
        def completed(done):
            self.transitions.discard(done)
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.warning("Temporary runtime switch retained its source: %s", error)
        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _switch_thread(self, thread_id, backend, generation):
        from agent.modules.workspaces import create_workspace_backend, get_workspace_backend_registry, is_backend_enabled
        descriptor = get_workspace_backend_registry(self.service.container).require(backend)
        if backend not in {"local", "daytona", "modal"} or not is_backend_enabled(descriptor, config_service=self.service.container.config_service):
            raise ValueError("The requested execution backend is not enabled.")
        rows, _ = await self.thread_status(thread_id)
        row = sorted(rows, key=lambda item: (":sub:" in item.thread_id, item.id))[0]
        ref = self.thread_ref(row)
        if (mirror_state(row).get("generation") or source_key(ref)) != generation:
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        state = mirror_state(row)
        source_status = await workspace_runtime_status(ref)
        source_lost = ref.backend != "local" and source_status in {"stopped", "destroyed"}
        if backend == ref.backend and not source_lost:
            return
        key = thread_storage_key(rows)
        from agent.modules.agent_runtime import get_active_session_registry, ThreadMutationConflictError
        registry = get_active_session_registry(self.service.container)
        with ExitStack() as reservation:
            lease_id = -int(key)
            with self.service.switcher.gate:
                self.service.switcher.assert_available(lease_id)
                if self.service.switcher.runs.get(lease_id):
                    raise ProjectWorkspaceError("Finish workspace runs before switching backends.", "workspace_busy")
                self.service.switcher.switching.add(lease_id)
            reservation.callback(self.service.switcher._release, lease_id)
            try:
                for item in rows:
                    reservation.enter_context(registry.reserve_thread_mutation(item.thread_id, require_idle=True))
            except ThreadMutationConflictError as exc:
                raise ProjectWorkspaceError(str(exc), "workspace_busy") from exc
            thread_ids = {item.thread_id for item in rows}
            if any(session["thread_id"] in thread_ids or any(session["thread_id"].startswith(thread + ":sub:") for thread in thread_ids)
                   for session in registry.list_active()):
                raise ProjectWorkspaceError("Finish workspace conversations before switching backends.", "workspace_busy")
            await self.service.switcher._assert_no_interrupts(thread_ids)
            async with self.thread_locks.setdefault(key, asyncio.Lock()):
                rows = await self._refresh_threads(rows, ref)
                if (mirror_state(rows[0]).get("generation") or source_key(ref)) != generation:
                    raise ProjectWorkspaceError("Thread runtime changed.", "stale_runtime")
                state = mirror_state(rows[0])
                from agent.modules.tools import get_coding_service
                coding = get_coding_service(self.service.container)
                if ref.backend != "local" and not source_lost and await coding.remote.workspace_processes(ref):
                    raise ProjectWorkspaceError("Stop sandbox processes before switching backends.", "workspace_busy")
                if ref.backend == "local" and any(not job.finished.is_set() and job.workspace == ref.locator
                                                 for job in (*coding.processes.jobs.values(), *coding.processes.retiring_jobs.values())):
                    raise ProjectWorkspaceError("Stop workspace processes before switching backends.", "workspace_busy")
                if source_lost:
                    revision = state.get("remote_revision") or state.get("revision")
                    if not revision:
                        raise ProjectWorkspaceError("No saved files are available for this temporary workspace.", "missing_data")
                    await asyncio.to_thread(self.temporary_storage.get_manifest, key, revision)
                else:
                    manifest = await self.service.transfer.capture(ref, self.temporary_storage, key)
                    revision = await asyncio.to_thread(self.temporary_storage.put_manifest, key, manifest)
                if ref.backend == "local":
                    state.update(path=ref.locator, revision=revision, remote_revision=revision, status="synced", conflicts=[])
                else:
                    if not state.get("path"):
                        from agent.modules.workspaces.service import temp_workspace_root
                        state["path"] = str(temp_workspace_root() / ("cloud-" + source_key(ref)[:32]))
                    state = await self._apply(self.temporary_storage, key, state, revision, generation)
                state["storage_key"] = key
                await self._save_threads(rows, ref, state)
                host_status = state["status"]
                if host_status != "synced" and not (source_lost and backend == ref.backend):
                    raise ProjectWorkspaceError("Resolve host synchronization before switching backends.", "host_sync_conflict")
                state = await self._cleanup_threads(rows, ref, state)
                async def record_target(created):
                    state["transition"] = {"target": created.model_dump(), "phase": "restoring"}
                    state["status"] = "syncing"
                    await self._save_threads(rows, ref, state)
                target = WorkspaceRef(locator=state["path"], metadata={"temp": True}) if backend == "local" else await create_workspace_backend(backend, on_created=record_target)
                target.metadata.update({name: value for name, value in ref.metadata.items()
                                        if name not in {"root", "status", "last_used_at", "_require_running"}})
                target.metadata["temp"] = True
                if backend != "local":
                    await self.service.transfer.restore(target, self.temporary_storage, key, revision)
                await self.service.transfer.verify(target, self.temporary_storage, key, revision)
                state.update(generation=hashlib.sha256(uuid4().bytes).hexdigest(), status=host_status, backend_status="started")
                if host_status == "synced":
                    state["last_synced_at"] = storage_timestamp(utcnow())
                state.pop("transition", None)
                if ref.backend != "local" and not source_lost:
                    state.setdefault("cleanup", []).append(ref.model_dump())
                session = await _session()
                async with session:
                    result = await session.execute(update(ThreadWorkspace).where(
                        ThreadWorkspace.id.in_([item.id for item in rows]), ThreadWorkspace.project_workspace_id.is_(None),
                        ThreadWorkspace.execution_backend == ref.backend, ThreadWorkspace.execution_locator == ref.locator,
                        or_(*(and_(ThreadWorkspace.id == item.id, ThreadWorkspace.execution_metadata_json == item.execution_metadata_json)
                              for item in rows)),
                    ).values(execution_backend=target.backend, execution_locator=target.locator, execution_label=target.label,
                             execution_metadata_json=json.dumps(target.metadata, sort_keys=True), working_dir=target.locator,
                             host_mirror_json=json.dumps(state), updated_at=utcnow()))
                    if result.rowcount != len(rows):
                        raise ProjectWorkspaceError("Thread workspace changed before activation.", "stale_runtime")
                    await session.commit()
                self._notify(thread_id=row.thread_id.split(":sub:", 1)[0])

    async def _retire_cloud(self, ref, *, delete=False):
        if ref.backend == "local":
            return
        session = await _session()
        async with session:
            threads = (await session.execute(select(ThreadWorkspace).where(
                ThreadWorkspace.execution_backend == ref.backend, ThreadWorkspace.execution_locator == ref.locator))).scalars().all()
            runtimes = (await session.execute(select(WorkspaceRuntime).where(WorkspaceRuntime.status != "destroyed"))).scalars().all()
        if threads or any((current := WorkspaceRef.model_validate_json(runtime.execution_json)).backend == ref.backend
                          and current.locator == ref.locator for runtime in runtimes):
            return
        from agent.modules.workspaces import get_workspace_backend_registry, call_workspace_backend_loader
        descriptor = get_workspace_backend_registry(self.service.container).require(ref.backend)
        loader = descriptor.delete_loader if delete else descriptor.stop_loader or descriptor.delete_loader
        if not loader:
            raise ValueError("The backend cannot retire unused sandboxes.")
        await call_workspace_backend_loader(ref.backend, loader, ref, in_thread=ref.backend == "daytona")

    async def _cleanup_threads(self, rows, ref, state):
        state = dict(state)
        pending = list(state.get("cleanup", []))
        transition = state.pop("transition", None)
        if transition:
            pending.append(dict(transition["target"], _delete_incomplete=True))
        if not pending:
            return state
        remaining = []
        for item in pending:
            item = dict(item)
            delete = item.pop("_delete_incomplete", False)
            try:
                await self._retire_cloud(WorkspaceRef.model_validate(item), delete=delete)
            except Exception as exc:
                remaining.append(dict(item, **({"_delete_incomplete": True} if delete else {})))
                state["last_error"] = "Old sandbox cleanup will be retried: " + str(exc)
        state["cleanup"] = remaining
        await self._save_threads(rows, ref, state)
        return state

    async def thread_status(self, thread_id):
        from agent.shared.thread_ids import resolve_thread_id
        thread_id = resolve_thread_id(thread_id).split(":sub:", 1)[0]
        groups = await self._thread_rows()
        for rows in groups.values():
            if any(row.thread_id == thread_id for row in rows):
                return rows, mirror_summary(rows[0])
        raise KeyError("Thread workspace not found.")

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._periodic())

    async def _guard(self, identity, operation):
        count, retry_at = self.failures.get(identity, (0, 0))
        if time.monotonic() < retry_at:
            return
        try:
            await operation()
            self.failures.pop(identity, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            count += 1
            self.failures[identity] = (count, time.monotonic() + min(30, 2 ** min(count, 5)))
            logger.debug("Host synchronization deferred identity=%s", identity, exc_info=True)
            if getattr(exc, "code", None) not in {"stale_runtime", "workspace_busy", "workspace_switching"}:
                try:
                    if identity[0] == "project":
                        session = await _session()
                        async with session:
                            workspace = await session.get(ProjectWorkspace, identity[1])
                        if workspace:
                            async with self.service.lock(workspace.id):
                                _, workspace, runtime = await self.service.rows(workspace.project_id, workspace.id)
                                state = mirror_state(workspace)
                                if state and runtime.generation == identity[2]:
                                    state.update(status="error", last_error=str(exc))
                                    await self._save_project(workspace, runtime, state)
                    else:
                        rows = (await self._thread_rows()).get(identity[1])
                        if rows:
                            ref = self.thread_ref(rows[0])
                            state = mirror_state(rows[0])
                            if state:
                                state.update(status="error", last_error=str(exc))
                                await self._save_threads(rows, ref, state)
                except Exception:
                    logger.debug("Could not save host synchronization failure", exc_info=True)

    async def _periodic(self):
        while not self.service.closing:
            try:
                session = await _session()
                async with session:
                    from agent.modules.projects.models import Project
                    projects = (await session.execute(select(ProjectWorkspace, WorkspaceRuntime).join(
                        WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id,
                    ).join(Project, Project.id == ProjectWorkspace.project_id).where(
                        Project.migration_status == "ready", ProjectWorkspace.kind != "legacy", WorkspaceRuntime.status.in_(["ready", "stopped"]),
                    ))).all()
                operations = [(('project', workspace.id, runtime.generation), lambda w=workspace, r=runtime: self._poll_project(w, r))
                              for workspace, runtime in projects]
                operations += [(('thread', key), lambda rows=rows: self._poll_threads(rows))
                               for key, rows in (await self._thread_rows()).items()]
                active = {identity for identity, _ in operations}
                for identity in tuple(self.jobs):
                    if identity not in active and self.jobs[identity].done():
                        self.jobs.pop(identity)
                        self.failures.pop(identity, None)
                for identity, operation in operations:
                    if identity not in self.jobs or self.jobs[identity].done():
                        self.jobs[identity] = asyncio.create_task(self._guard(identity, operation))
            except Exception:
                logger.debug("Host mirror discovery deferred", exc_info=True)
            await asyncio.sleep(1)

    async def close(self):
        await asyncio.gather(*tuple(self.transitions), return_exceptions=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        jobs = tuple(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.jobs.clear()
        try:
            groups = await self._thread_rows()
            results = await asyncio.gather(*(asyncio.wait_for(self._poll_threads(rows, force=True), timeout=10)
                                             for rows in groups.values()), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logger.debug("Temporary host mirror retained for retry at shutdown: %s", result)
        except Exception:
            logger.debug("Temporary host mirror shutdown discovery deferred", exc_info=True)
