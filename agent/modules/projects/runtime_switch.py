"""Durable, workspace-wide replacement of an execution provider."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
import json
import logging
from pathlib import Path
import threading
from uuid import uuid4

from sqlalchemy import or_, select, update

from agent.modules.projects.models import ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.workspaces import ProjectWorkspaceError, _session, storage_timestamp
from agent.modules.workspaces import WorkspaceRef
from agent.shared.infrastructure.db.base import utcnow

logger = logging.getLogger(__name__)
ACTIVE_PHASES = frozenset({"saving", "provisioning", "restoring", "verifying", "activating"})


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
        self.runs: dict[int, int] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.reservations: dict[int, ExitStack] = {}
        self.gate = threading.RLock()

    def assert_available(self, workspace_id: int, runtime=None):
        state = transition_state(runtime) if runtime is not None else None
        if workspace_id in self.switching or (state and state["phase"] in ACTIVE_PHASES):
            raise ProjectWorkspaceError("The workspace is switching execution backends. Please retry after it finishes.", "workspace_switching")

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
        from agent.modules.agent_runtime.active_sessions import get_active_session_registry
        get_active_session_registry(self.service.container).publish("workspace_runtime_changed", {
            "project_id": state["project_id"], "project_workspace_id": workspace_id,
            "operation_id": state["operation_id"], "phase": state["phase"],
        })

    async def _threads(self, workspace_id: int):
        from agent.modules.agent_runtime.models import BackgroundTaskRecord
        from agent.modules.conversations.models import ConversationThread
        from agent.modules.workspaces.models import ThreadWorkspace
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
            from agent.modules.tools.coding.service import get_coding_service
            try:
                busy = await get_coding_service(self.service.container).remote.workspace_processes(ref)
            except Exception as exc:
                raise ProjectWorkspaceError("Cannot verify sandbox process state. The current runtime has been retained.", "workspace_busy") from exc
            if busy:
                raise ProjectWorkspaceError("Stop running sandbox processes before switching backends.", "workspace_busy")

    async def request(self, project_id: int, backend: str, expected_generation: int, workspace_id: int | None = None):
        from agent.modules.agent_runtime.active_sessions import get_active_session_registry, ThreadMutationConflictError
        from agent.modules.workspaces.registry import get_workspace_backend_registry, _is_backend_enabled
        descriptor = get_workspace_backend_registry(self.service.container).require(backend)
        if backend not in {"local", "daytona", "modal"} or not _is_backend_enabled(descriptor, config_service=self.service.container.config_service):
            raise ValueError("The requested execution backend is not enabled.")
        project, workspace, runtime = await self.service.rows(project_id, workspace_id)
        workspace_id = workspace.id
        if self.service.closing or project.migration_status != "ready" or workspace.kind != "shared":
            raise ProjectWorkspaceError("Only an available shared project workspace can switch backends.", "workspace_busy")
        with self.gate:
            self.assert_available(workspace_id, runtime)
            if runtime.generation != expected_generation:
                raise ProjectWorkspaceError("Execution runtime changed; reload the workspace.", "stale_runtime")
            if self.runs.get(workspace_id) or self.service.lock(workspace_id).locked() or workspace_id in self.tasks:
                raise ProjectWorkspaceError("Finish or stop workspace operations before switching backends.", "workspace_busy")
            self.switching.add(workspace_id)
        handed_off = False
        state = None
        try:
            source = WorkspaceRef.model_validate_json(runtime.execution_json)
            if source.backend == backend:
                return {"operation_id": None, "workspace": (await self.service.binding(project_id, workspace_id)).model_dump(), "transition": transition_summary(runtime)}
            if runtime.status != "ready":
                raise ProjectWorkspaceError("Recover the current runtime before switching backends.", "runtime_unavailable")
            threads = await self._threads(workspace_id)
            registry = get_active_session_registry(self.service.container)
            if any(session["thread_id"] in threads or any(session["thread_id"].startswith(thread + ":sub:") for thread in threads)
                   for session in registry.list_active()):
                raise ProjectWorkspaceError("Finish or stop conversations using this workspace before switching backends.", "workspace_busy")
            reservation = ExitStack()
            self.reservations[workspace_id] = reservation
            try:
                for thread in sorted(threads):
                    reservation.enter_context(registry.reserve_thread_mutation(thread, require_idle=True))
            except ThreadMutationConflictError as exc:
                raise ProjectWorkspaceError(str(exc), "workspace_busy") from exc
            await self._assert_no_interrupts(threads)
            await self._assert_no_processes(workspace_id, source)
            previous = transition_state(runtime) or {}
            state = {"operation_id": uuid4().hex, "project_id": project_id, "phase": "saving", "target_backend": backend,
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

    async def _phase(self, workspace_id: int, state: dict, phase: str):
        state["phase"] = phase
        await self._write(workspace_id, state)

    async def _run(self, project_id: int, workspace_id: int, state: dict):
        activated = False
        try:
            async with self.service.lock(workspace_id):
                project, workspace, runtime = await self.service.rows(project_id, workspace_id)
                if runtime.generation != state["source_generation"]:
                    raise ProjectWorkspaceError("Execution runtime changed before switching.", "stale_runtime")
                saved = await self.service._checkpoint(project_id, workspace_id, runtime.generation)
                state["revision"] = saved["revision"]
                session = await _session()
                async with session:
                    await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace_id).values(pinned_revision=saved["revision"]))
                    await session.commit()
                await self._phase(workspace_id, state, "provisioning")
                if state["target_backend"] == "local":
                    destination = self.service.runtime_root / str(workspace_id) / ("switch-" + state["operation_id"])
                    target = WorkspaceRef(backend="local", locator=str(destination), label=project.label)
                    state["target"] = target.model_dump()
                    await self._phase(workspace_id, state, "restoring")
                    await asyncio.to_thread(self.service.transfer.materialize, self.service.storage, str(workspace_id), saved["revision"], destination)
                else:
                    from agent.modules.workspaces import create_workspace_backend
                    async def remember_target(ref):
                        state["target"] = ref.model_dump()
                        await self._write(workspace_id, state)
                    target = await create_workspace_backend(state["target_backend"], label="k41-switch-" + state["operation_id"],
                                                            on_created=remember_target)
                    state["target"] = target.model_dump()
                    await self._phase(workspace_id, state, "restoring")
                    await self.service.transfer.restore(target, self.service.storage, str(workspace_id), saved["revision"])
                await self._phase(workspace_id, state, "verifying")
                await self.service.transfer.verify(target, self.service.storage, str(workspace_id), saved["revision"])
                from agent.modules.workspaces import merge_ready_workspace_metadata
                target = merge_ready_workspace_metadata(WorkspaceRef.model_validate(state["source"]), target)
                if target.metadata.get("source") == "github":
                    target.metadata["repository_path"] = target.locator if target.backend == "local" else "."
                target = target.model_copy(update={"label": project.label})
                state["target"] = target.model_dump()
                await self._phase(workspace_id, state, "activating")
                state["phase"] = "completed"
                if state["source"]["backend"] != "local":
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
                state.update(phase="failed", error=message)
                if state.get("target"):
                    state["cleanup"].append({"ref": state["target"], "action": "delete", "operation_id": state["operation_id"]})
            await asyncio.shield(self._write(workspace_id, state))
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
            session = await _session()
            async with session:
                runtimes = (await session.execute(select(WorkspaceRuntime).where(WorkspaceRuntime.status != "destroyed"))).scalars().all()
            if any((current := WorkspaceRef.model_validate_json(runtime.execution_json)).backend == ref.backend and current.locator == ref.locator
                   for runtime in runtimes):
                remaining.append(item)
                continue
            try:
                if ref.backend == "local":
                    import shutil
                    raw_path = Path(ref.locator)
                    path = raw_path.resolve()
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
                if state["phase"] in ACTIVE_PHASES:
                    state.update(phase="failed", error="The application restarted before the switch completed. The source runtime is unchanged.")
                    if state.get("target"):
                        state["cleanup"].append({"ref": state["target"], "action": "delete", "operation_id": state["operation_id"]})
                    await self._write(workspace_id, state)
                await self._unpin(workspace_id, state)
                if state.get("cleanup"):
                    await self._cleanup(workspace_id, state)

    async def close(self):
        # Do not cancel a provider allocation or a filesystem worker mid-flight.
        await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)
