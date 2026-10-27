"""Durable, workspace-wide replacement of an execution provider."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, ExitStack, contextmanager
import json
import logging
from pathlib import Path
import threading
from uuid import uuid4

from sqlalchemy import or_, select, update

from agent.modules.projects.models import ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.workspaces import ProjectWorkspaceError, _session, storage_timestamp
from agent.modules.workspaces import WorkspaceRef
from agent.modules.workspaces.policies import MANUAL_APPLY, sync_policy
from agent.shared.infrastructure.db.base import utcnow

logger = logging.getLogger(__name__)
ACTIVE_PHASES = frozenset({"saving", "provisioning", "restoring", "verifying", "activating", "allocation_unknown"})


def transition_state(runtime) -> dict | None:
    return json.loads(runtime.transition_json or "null")


def transition_summary(runtime) -> dict | None:
    state = transition_state(runtime)
    if state is None:
        return None
    return {key: state.get(key) for key in (
        "operation_id", "phase", "target_backend", "revision", "error", "cleanup_error", "started_at", "updated_at",
    )}


class WorkspaceRuntimeSwitcher:
    def __init__(self, service):
        self.service = service
        self.switching: set[int] = set()
        self.recovering: set[int] = set()
        self.runs: dict[int, int] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.reservations: dict[int, ExitStack] = {}
        self.gate = threading.RLock()

    def assert_available(self, workspace_id: int, runtime=None):
        state = transition_state(runtime) if runtime is not None else None
        if workspace_id in self.switching:
            raise ProjectWorkspaceError("The workspace is changing its execution environment or synchronizing files. Please retry after it finishes.", "workspace_switching")
        if state and state["phase"] in ACTIVE_PHASES:
            if state["phase"] == "allocation_unknown":
                raise ProjectWorkspaceError("The previous switch has an unknown provider allocation. Reconcile the provider inventory, then abandon the switch before retrying.", "workspace_switching")
            raise ProjectWorkspaceError("The workspace is changing its execution environment or synchronizing files. Please retry after it finishes.", "workspace_switching")

    @contextmanager
    def run_lease(self, workspace_id: int):
        """Admission and switch reservation share one synchronous critical section."""
        with self.gate:
            self.assert_available(workspace_id)
            self.runs[workspace_id] = self.runs.get(workspace_id, 0) + 1
        try:
            yield
        finally:
            with self.gate:
                remaining = self.runs[workspace_id] - 1
                if remaining:
                    self.runs[workspace_id] = remaining
                else:
                    self.runs.pop(workspace_id, None)

    def _release(self, workspace_id: int):
        reservation = self.reservations.pop(workspace_id, None)
        if reservation:
            reservation.close()
        with self.gate:
            self.switching.discard(workspace_id)

    async def _write(self, workspace_id: int, state: dict):
        state["updated_at"] = storage_timestamp(utcnow())
        session = await _session()
        async with session:
            await session.execute(update(WorkspaceRuntime).where(
                WorkspaceRuntime.project_workspace_id == workspace_id,
            ).values(transition_json=json.dumps(state), updated_at=utcnow()))
            await session.commit()
        self._notify(workspace_id, state)

    def _notify(self, workspace_id: int, state: dict):
        from agent.modules.agent_runtime import get_active_session_registry
        get_active_session_registry(self.service.container).publish("workspace_runtime_changed", {
            "project_id": state["project_id"], "project_workspace_id": workspace_id,
            "operation_id": state["operation_id"], "phase": state["phase"],
        })

    async def _threads(self, workspace_id: int):
        from agent.modules.agent_runtime import BackgroundTaskRecord
        from agent.modules.conversations import ConversationThread
        from agent.modules.workspaces import ThreadWorkspace
        session = await _session()
        async with session:
            threads = set((await session.execute(select(ThreadWorkspace.thread_id).where(
                ThreadWorkspace.project_workspace_id == workspace_id,
            ))).scalars())
            tasks = (await session.execute(select(BackgroundTaskRecord).where(
                BackgroundTaskRecord.project_workspace_id == workspace_id,
                BackgroundTaskRecord.deleted_at.is_(None),
            ))).scalars().all()
        threads.update(task.thread_id for task in tasks)
        if threads:
            session = await _session()
            async with session:
                children = (await session.execute(select(ConversationThread.thread_id).where(ConversationThread.deleted_at.is_(None), or_(
                    *(ConversationThread.thread_id.startswith(thread + ":sub:", autoescape=True) for thread in threads),
                )))).scalars().all()
            threads.update(children)
        if any(task.status in {"pending", "running", "retrying"} for task in tasks):
            raise ProjectWorkspaceError("Finish or stop background tasks before switching backends.", "workspace_busy")
        return threads

    async def _assert_no_interrupts(self, threads: set[str]):
        if not threads:
            return
        from agent.modules.workflows import get_workflow_graph, make_run_config
        graph = get_workflow_graph("react_agent")
        for thread_id in threads:
            try:
                snapshot = await graph.aget_state(make_run_config(thread_id=thread_id))
            except Exception as exc:
                raise ProjectWorkspaceError("Cannot verify pending workspace approvals. Retry after conversation history is available.", "workspace_busy") from exc
            if getattr(snapshot, "next", ()) or any(getattr(task, "interrupts", ()) for task in getattr(snapshot, "tasks", ())):
                raise ProjectWorkspaceError("Resolve pending conversation requests before switching backends.", "workspace_busy")

    async def _assert_no_processes(self, workspace_id: int, ref: WorkspaceRef):
        if any(key[0] == workspace_id for key in self.service.running_processes):
            raise ProjectWorkspaceError("Stop running workspace processes before switching backends.", "workspace_busy")
        coding = self.service.container._coding_service
        if coding and any((job.context.project_workspace_id == workspace_id
                           or (ref.backend == "local" and Path(job.workspace).resolve() == Path(ref.locator).resolve()))
                          and not job.finished.is_set()
                          for job in (*coding.processes.jobs.values(), *coding.processes.retiring_jobs.values())):
            raise ProjectWorkspaceError("Stop running workspace processes before switching backends.", "workspace_busy")
        if ref.backend != "local":
            from agent.modules.tools import get_coding_service
            try:
                busy = await get_coding_service(self.service.container).remote.workspace_processes(ref)
            except Exception as exc:
                raise ProjectWorkspaceError("Cannot verify sandbox process state. The current runtime has been retained.", "workspace_busy") from exc
            if busy:
                raise ProjectWorkspaceError("Stop running sandbox processes before switching backends.", "workspace_busy")

    def _reserve(self, workspace_id: int, runtime, expected_generation: int):
        with self.gate:
            self.assert_available(workspace_id, runtime)
            if runtime.generation != expected_generation:
                raise ProjectWorkspaceError("Execution runtime changed; reload the workspace.", "stale_runtime")
            if self.runs.get(workspace_id) or self.service.lock(workspace_id).locked() or workspace_id in self.tasks:
                raise ProjectWorkspaceError("Finish or stop workspace operations before changing its environment or files.", "workspace_busy")
            self.switching.add(workspace_id)

    async def reserve_idle(self, workspace, runtime, expected_generation: int, *, allow_unavailable=False):
        """Reserve the single writer across backend changes and linked-directory sync."""
        from agent.modules.agent_runtime import get_active_session_registry, ThreadMutationConflictError
        workspace_id = workspace.id
        self._reserve(workspace_id, runtime, expected_generation)
        try:
            threads = await self._threads(workspace_id)
            registry = get_active_session_registry(self.service.container)
            if any(session["thread_id"] in threads or any(session["thread_id"].startswith(thread + ":sub:") for thread in threads)
                   for session in registry.list_active()):
                raise ProjectWorkspaceError("Finish or stop conversations using this workspace first.", "workspace_busy")
            reservation = ExitStack()
            self.reservations[workspace_id] = reservation
            try:
                for thread in sorted(threads):
                    reservation.enter_context(registry.reserve_thread_mutation(thread, require_idle=True))
            except ThreadMutationConflictError as exc:
                raise ProjectWorkspaceError(str(exc), "workspace_busy") from exc
            await self._assert_no_interrupts(threads)
            if not allow_unavailable:
                await self._assert_no_processes(workspace_id, WorkspaceRef.model_validate_json(runtime.execution_json))
        except BaseException:
            self._release(workspace_id)
            raise

    async def request(self, project_id: int, backend: str, expected_generation: int, workspace_id: int | None = None,
                      *, preview_token: str | None = None):
        from agent.modules.workspaces import get_workspace_backend_registry, is_backend_enabled
        descriptor = get_workspace_backend_registry(self.service.container).require(backend)
        if backend not in {"local", "daytona", "modal"} or not is_backend_enabled(descriptor, config_service=self.service.container.config_service):
            raise ValueError("The requested execution backend is not enabled.")
        project, workspace, runtime = await self.service.rows(project_id, workspace_id)
        workspace_id = workspace.id
        if self.service.closing or project.migration_status != "ready" or workspace.kind != "shared":
            raise ProjectWorkspaceError("Only an available shared project workspace can switch backends.", "workspace_busy")
        source = WorkspaceRef.model_validate_json(runtime.execution_json)
        if backend == "local" and source.backend != "local" and sync_policy(workspace) == MANUAL_APPLY and not preview_token:
            raise ProjectWorkspaceError("Preview local changes before continuing locally.", "local_preview_required")
        local_rebind = (backend == source.backend == "local"
                        and Path(source.locator).resolve() != self.service.host_mirrors.project_path(workspace, runtime).resolve())
        if source.backend == backend and not local_rebind:
            self._reserve(workspace_id, runtime, expected_generation)
            try:
                return {"operation_id": None, "workspace": (await self.service.binding(project_id, workspace_id)).model_dump(), "transition": transition_summary(runtime)}
            finally:
                self._release(workspace_id)
        await self.reserve_idle(workspace, runtime, expected_generation)
        handed_off = False
        state = None
        try:
            if runtime.status != "ready":
                raise ProjectWorkspaceError("Recover the current runtime before switching backends.", "runtime_unavailable")
            previous = transition_state(runtime) or {}
            state = {"operation_id": uuid4().hex, "project_id": project_id, "phase": "saving", "target_backend": backend,
                     "kind": "switch", "preview_token": preview_token,
                     "source": source.model_dump(), "source_generation": expected_generation, "target": None,
                     "revision": None, "error": "", "cleanup_error": "", "cleanup": previous.get("cleanup", []),
                     "started_at": storage_timestamp(utcnow()), "previous_pin": workspace.pinned_revision}
            await self._write(workspace_id, state)
            task = asyncio.create_task(self._run(project_id, workspace_id, state))
            self.tasks[workspace_id] = task

            def forget(done):
                if self.tasks.get(workspace_id) is done:
                    self.tasks.pop(workspace_id, None)
                if not done.cancelled() and (error := done.exception()) is not None:
                    logger.error("Runtime switch requires recovery workspace=%s", workspace_id,
                                 exc_info=(type(error), error, error.__traceback__))

            task.add_done_callback(forget)
            handed_off = True
            return {"operation_id": state["operation_id"], "workspace": (await self.service.binding(project_id, workspace_id)).model_dump(),
                    "transition": {key: state.get(key) for key in ("operation_id", "phase", "target_backend", "error")}}
        except BaseException:
            if state is not None and not handed_off:
                state.update(phase="failed", error="The switch request was interrupted before execution.")
                await asyncio.shield(self._write(workspace_id, state))
            raise
        finally:
            if not handed_off:
                self._release(workspace_id)

    async def abandon(self, project_id: int, workspace_id: int | None = None, *,
                      expected_operation_id: str | None = None):
        """Resolve an unknown allocation after the operator reconciled provider inventory."""
        project, workspace, runtime = await self.service.rows(project_id, workspace_id)
        workspace_id = workspace.id
        async with self.service.lock(workspace_id):
            if workspace_id in self.switching or workspace_id in self.tasks:
                raise ProjectWorkspaceError("The runtime switch is still running.", "workspace_busy")
            session = await _session()
            async with session:
                current = (await session.execute(select(WorkspaceRuntime).where(
                    WorkspaceRuntime.project_workspace_id == workspace_id,
                ))).scalar_one()
            state = transition_state(current)
            if state is None or state.get("phase") != "allocation_unknown":
                raise ProjectWorkspaceError("There is no unresolved allocation to abandon.", "transition_unavailable")
            if expected_operation_id and state.get("operation_id") != expected_operation_id:
                raise ProjectWorkspaceError("The runtime transition changed; reload the workspace.", "stale_runtime")
            # A late provider response may still identify the sandbox created by the abandoned attempt.
            from agent.modules.workspaces.transitions import RuntimeTransitionCoordinator
            try:
                target = await RuntimeTransitionCoordinator.find_allocation(
                    state["target_backend"], state["operation_id"])
            except Exception:
                target = None
            cleanup = state.setdefault("cleanup", [])
            if target is not None and not any(item.get("ref") == target.model_dump() for item in cleanup):
                state["target"] = target.model_dump()
                cleanup.append({"ref": target.model_dump(), "action": "delete", "operation_id": state["operation_id"]})
            state.update(phase="failed",
                         error="The unresolved allocation was abandoned. Reconcile the provider inventory before retrying.")
            await self._write(workspace_id, state)
            await self._unpin(workspace_id, state)
            await self._cleanup(workspace_id, state)
        return await self.service.binding(project_id, workspace_id)

    async def _phase(self, workspace_id: int, state: dict, phase: str):
        if phase == "provisioning" and state.get("phase") != "provisioning" and state.get("target_backend") != "local":
            self.service.metrics.provision_count += 1
        state["phase"] = phase
        await self._write(workspace_id, state)

    async def replace(self, project, workspace, runtime, backend, *, preview_token=None):
        """Recovery shares switch journaling; the caller already owns the writer lock."""
        self.assert_available(workspace.id, runtime)
        self.service.metrics.recovery_count += 1
        if backend == "local" and sync_policy(workspace) == MANUAL_APPLY and not preview_token:
            raise ProjectWorkspaceError("Preview saved changes before continuing locally.", "local_preview_required")
        previous = transition_state(runtime) or {}
        state = {"operation_id": uuid4().hex, "project_id": project.id, "phase": "saving", "target_backend": backend,
                 "kind": "recovery", "preview_token": preview_token,
                 "source": WorkspaceRef.model_validate_json(runtime.execution_json).model_dump(),
                 "source_generation": runtime.generation, "target": None, "revision": workspace.head_revision,
                 "error": "", "cleanup_error": "", "cleanup": previous.get("cleanup", []),
                 "started_at": storage_timestamp(utcnow()), "previous_pin": workspace.pinned_revision}
        with self.gate:
            self.recovering.add(workspace.id)
            self.switching.add(workspace.id)
        try:
            await self._write(workspace.id, state)
            await self._run(project.id, workspace.id, state, lock_held=True)
        finally:
            self.recovering.discard(workspace.id)
            self._release(workspace.id)
        if state["phase"] != "completed":
            raise ProjectWorkspaceError("Project restore failed: " + (state.get("error") or "Recovery did not finish."), "runtime_unavailable")
        return await self.service.binding(project.id, workspace.id)

    async def _run(self, project_id: int, workspace_id: int, state: dict, *, lock_held=False):
        activated = False
        try:
            async with AsyncExitStack() as stack:
                if not lock_held:
                    await stack.enter_async_context(self.service.lock(workspace_id))
                project, workspace, runtime = await self.service.rows(project_id, workspace_id)
                if runtime.generation != state["source_generation"]:
                    raise ProjectWorkspaceError("Execution runtime changed before switching.", "stale_runtime")
                recovery = state.get("kind") == "recovery"
                manual = sync_policy(workspace) == MANUAL_APPLY
                saved = ({"revision": state["revision"], "saved": True} if recovery else
                         await self.service._checkpoint(project_id, workspace_id, runtime.generation,
                                                        sync_host=state["target_backend"] == "local" and not manual))
                if not saved.get("revision"):
                    raise ProjectWorkspaceError("No saved files are available for recovery.", "missing_data")
                await asyncio.to_thread(self.service.storage.get_manifest, str(workspace_id), saved["revision"])
                if state["target_backend"] == "local" and not manual and not recovery and (saved.get("host_mirror") or {}).get("status") != "synced":
                    raise ProjectWorkspaceError("Resolve host synchronization before switching backends.", "host_sync_conflict")
                state["revision"] = saved["revision"]
                session = await _session()
                async with session:
                    await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace_id).values(pinned_revision=saved["revision"]))
                    await session.commit()
                await self._phase(workspace_id, state, "provisioning")
                if state["target_backend"] == "local":
                    _, workspace, runtime = await self.service.rows(project_id, workspace_id)
                    destination = self.service.host_mirrors.project_path(workspace, runtime)
                    target = WorkspaceRef(backend="local", locator=str(destination), label=project.label)
                    state["target"] = target.model_dump()
                    await self._phase(workspace_id, state, "restoring")
                    if manual:
                        from agent.modules.projects.local_sync import apply_workspace_revision
                        applied = await apply_workspace_revision(self.service.transfer, self.service.storage, str(workspace_id),
                            saved["revision"], workspace.local_sync_revision, destination, runtime.generation,
                            preview_token=state.get("preview_token"),
                            journal=self.service.runtime_root / str(workspace_id) / "local-sync.json")
                        saved["revision"] = applied["merged_revision"]
                        state["local_revision"] = saved["revision"]
                    elif recovery:
                        mirror = await self.service.host_mirrors.after_checkpoint(workspace, runtime, saved["revision"])
                        if mirror["status"] != "synced":
                            raise ProjectWorkspaceError("Resolve host synchronization before continuing locally.", "host_sync_conflict")
                else:
                    from agent.modules.workspaces.transitions import RuntimeTransitionCoordinator
                    async def remember_target(ref):
                        state["target"] = ref.model_dump()
                        await self._write(workspace_id, state)
                    target = await RuntimeTransitionCoordinator.prepare_cloud(state["target_backend"], self.service.transfer,
                        self.service.storage, str(workspace_id), saved["revision"], on_created=remember_target,
                        phase=lambda value: self._phase(workspace_id, state, value), label="k41-switch-" + state["operation_id"])
                    state["target"] = target.model_dump()
                if state["target_backend"] == "local":
                    await self._phase(workspace_id, state, "verifying")
                    await self.service.transfer.verify(target, self.service.storage, str(workspace_id), saved["revision"])
                from agent.modules.workspaces import merge_ready_workspace_metadata
                target = merge_ready_workspace_metadata(WorkspaceRef.model_validate(state["source"]), target)
                if target.metadata.get("source") == "github":
                    target.metadata["repository_path"] = target.locator if target.backend == "local" else "."
                target = target.model_copy(update={"label": project.label})
                target.metadata.update(files_restored=True, environment_state="unknown")
                state["target"] = target.model_dump()
                await self._phase(workspace_id, state, "activating")
                state["phase"] = "completed"
                if state["source"]["backend"] != "local" and not recovery:
                    state["cleanup"].append({"ref": state["source"], "action": "retire", "operation_id": state["operation_id"]})
                await self.service._publish_runtime(project, workspace, target, runtime.generation + 1, "ready", transition=state)
                activated = True
                self.service.active.add(workspace_id)
                await self._unpin(workspace_id, state)
            # The verified destination can serve new runs while the unused source is retired.
            self._release(workspace_id)
            self._notify(workspace_id, state)
            await self._cleanup(workspace_id, state)
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                message = "Runtime switching was interrupted. The saved source revision is retained."
            else:
                message = str(exc)
            if not activated:
                # Commit may have succeeded even when its acknowledgement was lost.
                _, _, current = await self.service.rows(project_id, workspace_id)
                published = transition_state(current) or {}
                activated = (current.generation == state["source_generation"] + 1
                             and published.get("operation_id") == state["operation_id"]
                             and published.get("phase") == "completed")
            if activated:
                self._release(workspace_id)
                state.update(phase="completed", cleanup_error=message)
            else:
                unknown = state["phase"] == "provisioning" and not state.get("target") and isinstance(exc, (TimeoutError, ConnectionError, asyncio.CancelledError))
                state.update(phase="allocation_unknown" if unknown else "failed", error=message)
                if state.get("target") and state["target"]["backend"] != "local":
                    state["cleanup"].append({"ref": state["target"], "action": "delete", "operation_id": state["operation_id"]})
            await asyncio.shield(self._write(workspace_id, state))
            if state["phase"] != "allocation_unknown":
                await asyncio.shield(self._unpin(workspace_id, state))
            await asyncio.shield(self._cleanup(workspace_id, state))
            logger.warning("Runtime switch outcome workspace=%s activated=%s: %s", workspace_id, activated, message)
        finally:
            self._release(workspace_id)

    async def _unpin(self, workspace_id: int, state: dict):
        session = await _session()
        async with session:
            await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace_id,
                ProjectWorkspace.pinned_revision == state.get("revision")).values(pinned_revision=state.get("previous_pin")))
            await session.commit()

    async def _cleanup(self, workspace_id: int, state: dict):
        from agent.modules.workspaces import get_workspace_backend_registry, call_workspace_backend_loader
        remaining = []
        errors = []
        for item in state.get("cleanup", []):
            ref = WorkspaceRef.model_validate(item["ref"])
            # Never stop a sandbox still owning another live workspace, or one rebound through a legacy alias.
            from agent.modules.workspaces.runtime import runtime_is_owned
            if await runtime_is_owned(ref):
                remaining.append(item)
                continue
            try:
                if ref.backend == "local":
                    import shutil
                    from agent.modules.workspaces.host_mirror import mirror_state
                    raw_path = Path(ref.locator)
                    path = raw_path.resolve()
                    session = await _session()
                    async with session:
                        owned = (await session.execute(select(ProjectWorkspace))).scalars().all()
                    if any(mirror_state(row).get("path") and Path(mirror_state(row)["path"]).resolve() == path for row in owned):
                        # A legacy cleanup record may now name the adopted canonical host directory.
                        continue
                    expected = (self.service.runtime_root / str(workspace_id) / ("switch-" + item.get("operation_id", state["operation_id"]))).resolve()
                    if path != expected or not path.is_relative_to(self.service.runtime_root.resolve()) or raw_path.is_symlink():
                        raise ValueError("Refusing to remove an unmanaged runtime directory.")
                    if path.exists():
                        await asyncio.to_thread(shutil.rmtree, path)
                else:
                    registry = get_workspace_backend_registry(self.service.container)
                    descriptor = registry.require(ref.backend)
                    loader = descriptor.stop_loader if item["action"] == "retire" else descriptor.delete_loader
                    # Modal termination is its stop operation and retains no running process state.
                    loader = loader or (descriptor.delete_loader if ref.backend == "modal" else "")
                    if not loader:
                        raise ValueError("The backend does not support runtime retirement.")
                    await call_workspace_backend_loader(ref.backend, loader, ref, in_thread=ref.backend == "daytona")
                coding = self.service.container._coding_service
                if coding:
                    for key in tuple(coding.remote.clients):
                        if key[:2] == (ref.backend, ref.locator):
                            coding.remote.clients.pop(key, None)
            except Exception as exc:
                remaining.append(item)
                errors.append(str(exc))
        state["cleanup"] = remaining
        state["cleanup_error"] = "; ".join(errors)
        await self._write(workspace_id, state)

    async def recover(self):
        """Never resume user commands or publish a partially restored destination."""
        session = await _session()
        async with session:
            rows = (await session.execute(select(WorkspaceRuntime))).scalars().all()
        for runtime in rows:
            workspace_id = runtime.project_workspace_id
            if workspace_id in self.switching or workspace_id in self.tasks:
                continue
            state = transition_state(runtime)
            if state is None:
                continue
            async with self.service.lock(workspace_id):
                if workspace_id in self.switching or workspace_id in self.tasks:
                    continue
                session = await _session()
                async with session:
                    current = (await session.execute(select(WorkspaceRuntime).where(
                        WorkspaceRuntime.project_workspace_id == workspace_id,
                    ))).scalar_one()
                state = transition_state(current)
                if state is None:
                    continue
                if state["phase"] == "allocation_unknown" or (state["phase"] == "provisioning" and not state.get("target")):
                    from agent.modules.workspaces.transitions import RuntimeTransitionCoordinator
                    try:
                        target = await RuntimeTransitionCoordinator.find_allocation(state["target_backend"], state["operation_id"])
                    except Exception:
                        target = None
                    if target:
                        state["target"] = target.model_dump()
                        state["phase"] = "restoring"
                    else:
                        state.update(phase="allocation_unknown", error="Allocation outcome is unknown. Reconcile provider inventory before retrying.")
                        await self._write(workspace_id, state)
                        continue
                if state["phase"] in ACTIVE_PHASES:
                    state.update(phase="failed", error="The application restarted before the switch completed. The source runtime is unchanged.")
                    if state.get("target") and state["target"]["backend"] != "local":
                        state["cleanup"].append({"ref": state["target"], "action": "delete", "operation_id": state["operation_id"]})
                    await self._write(workspace_id, state)
                await self._unpin(workspace_id, state)
                if state.get("cleanup"):
                    await self._cleanup(workspace_id, state)

    async def close(self):
        # Do not cancel a provider allocation or a filesystem worker mid-flight.
        await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)
