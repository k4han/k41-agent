"""Local project homes and conversation-owned execution workspaces."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import tempfile

from sqlalchemy import or_, select, update

from agent.modules.projects.models import Project, ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.storage import file_chunks
from agent.modules.projects.workspaces import ProjectWorkspaceError, _binding, _session, register_workspace, source_key
from agent.modules.workspaces import WorkspaceRef
from agent.shared.infrastructure.db.base import utcnow


class ProjectSessionWorkspaces:
    def __init__(self, service):
        self.service = service

    async def copy_revision(self, source_id, target_id, revision):
        if not revision or source_id == target_id:
            return revision
        store = self.service.storage

        def copy():
            source, target = str(source_id), str(target_id)
            manifest = store.get_manifest(source, revision)
            for entry in manifest["entries"].values():
                if entry["type"] == "file":
                    for chunk in file_chunks(entry):
                        digest = chunk["checksum"]
                        if not store.has_blob(target, digest):
                            store.put_blob(target, digest, store.get_blob(source, digest))
            return store.put_manifest(target, manifest)

        return await asyncio.to_thread(copy)

    async def prepare_local(self, project_id):
        service = self.service
        async with service.selection_locks.setdefault(project_id, asyncio.Lock()):
            project, workspace, runtime = await service.rows(project_id)
            if service.closing or not service.container._persistence_ready:
                raise ProjectWorkspaceError("Project storage is temporarily unavailable.")
            if project.migration_status != "ready":
                raise ProjectWorkspaceError("Choose the main workspace before continuing.", "migration_required")
            execution = WorkspaceRef.model_validate_json(runtime.execution_json)
            if project.local_workspace_id:
                if execution.backend != "local" or not Path(execution.locator).is_dir():
                    raise ProjectWorkspaceError("The project local workspace is unavailable.", "runtime_unavailable")
                return _binding(project, workspace, runtime)
            if execution.backend == "local":
                async with await _session() as session:
                    await session.execute(update(Project).where(Project.id == project_id).values(local_workspace_id=workspace.id))
                    await session.commit()
                return _binding(project, workspace, runtime)

            source = WorkspaceRef.model_validate_json(workspace.source_json)
            path = Path(source.locator) if source.backend == "local" else service.runtime_root / ("project-" + str(project_id)) / "local"
            # An original directory is adopted as-is. Only a cloud-only project is hydrated.
            if source.backend == "local" and not path.is_dir():
                raise ProjectWorkspaceError("The linked local directory is unavailable: " + str(path), "runtime_unavailable")
            if source.backend != "local" and not workspace.head_revision:
                raise ProjectWorkspaceError("Save or synchronize the sandbox before opening a local session.", "missing_data")
            ref = WorkspaceRef(locator=str(path), label=project.label,
                               metadata={key: value for key, value in execution.metadata.items()
                                         if key in {"source", "repository_id", "repository_full_name", "installation_id", "default_branch", "branch", "base_branch"}})
            if ref.metadata.get("source") == "github":
                ref.metadata["repository_path"] = str(path)
            async with await _session() as session:
                local = await register_workspace(session, project, ref, identity="local")
                await session.execute(update(WorkspaceRuntime).where(
                    WorkspaceRuntime.project_workspace_id == local.project_workspace_id,
                ).values(status="restoring"))
                await session.commit()
            async with AsyncExitStack() as stack:
                if source.backend != "local":
                    await stack.enter_async_context(service.lock(workspace.id))
                await stack.enter_async_context(service.lock(local.project_workspace_id))
                # A saved revision can be read while the sandbox continues running.
                _, workspace, _ = await service.rows(project_id, workspace.id)
                seed_revision = None
                if source.backend != "local":
                    try:
                        _, local_row, _ = await service.rows(project_id, local.project_workspace_id)
                        seed_revision = json.loads(local_row.host_mirror_json or "{}").get("seed_revision")
                        if not seed_revision:
                            seed_revision = await self.copy_revision(workspace.id, local.project_workspace_id, workspace.head_revision)
                            async with await _session() as session:
                                await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == local.project_workspace_id).values(
                                    pinned_revision=seed_revision, host_mirror_json=json.dumps({"seed_revision": seed_revision})))
                                await session.commit()
                        if not path.exists():
                            from agent.modules.projects.local_sync import _run_writer
                            from agent.modules.projects.snapshot_worker import verify
                            path.parent.mkdir(parents=True, exist_ok=True)
                            with tempfile.TemporaryDirectory(prefix=".local-", dir=path.parent) as directory:
                                stage = Path(directory) / "workspace"
                                def restore():
                                    service.transfer.materialize(service.storage, str(local.project_workspace_id), seed_revision, stage)
                                    verify(str(stage), service.storage.get_manifest(str(local.project_workspace_id), seed_revision))
                                await _run_writer(restore)
                                stage.rename(path)
                    except Exception as exc:
                        raise ProjectWorkspaceError("The saved sandbox files could not be restored: " + str(exc), "missing_data") from exc
                async with await _session() as session:
                    await session.execute(update(WorkspaceRuntime).where(
                        WorkspaceRuntime.project_workspace_id == local.project_workspace_id,
                    ).values(status="ready"))
                    await session.commit()
                await service._checkpoint(project_id, local.project_workspace_id, sync_host=False)
                if seed_revision:
                    await self.copy_revision(local.project_workspace_id, workspace.id, seed_revision)
                async with await _session() as session:
                    await session.execute(update(Project).where(Project.id == project_id).values(
                        local_workspace_id=local.project_workspace_id, workspace_json=local.model_dump_json()))
                    # Cloud chat workspaces never write the local home in the background.
                    await session.execute(update(ProjectWorkspace).where(
                        ProjectWorkspace.project_id == project_id, ProjectWorkspace.kind.in_(["shared", "session"]),
                    ).values(sync_policy="manual_apply"))
                    if source.backend != "local":
                        await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace.id).values(
                            local_sync_revision=seed_revision))
                        await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == local.project_workspace_id).values(pinned_revision=None))
                    await session.commit()
            service.monitor(local.project_workspace_id)
            return await service.binding(project_id, local.project_workspace_id)

    async def request(self, project_id, thread_id, backend, generation, workspace_id, *, preview_token=None):
        from agent.shared.thread_ids import resolve_thread_id
        thread_id = resolve_thread_id(thread_id).split(":sub:", 1)[0]
        service = self.service
        binding = await service.resolve_thread(thread_id)
        if binding and workspace_id is None:
            workspace_id = binding.project_workspace_id
        if not binding or binding.project_id != project_id or binding.project_workspace_id != workspace_id:
            raise ProjectWorkspaceError("The conversation workspace changed; reload the session.", "stale_runtime")
        project, workspace, runtime = await service.rows(project_id, workspace_id)
        if runtime.generation != generation:
            raise ProjectWorkspaceError("Execution runtime changed; reload the session.", "stale_runtime")
        service._assert_recovery_backend(backend)
        source = WorkspaceRef.model_validate_json(runtime.execution_json)
        if source.backend == backend:
            service.switcher.assert_available(workspace.id, runtime)
            return {"operation_id": None, "workspace": binding.model_dump(), "transition": None}
        if workspace.kind not in {"shared", "session"}:
            raise ProjectWorkspaceError("This workspace cannot switch session backends.", "workspace_busy")
        local = await service.prepare_chat(project_id)
        extra = {"thread_id": thread_id, "source_workspace_id": workspace.id,
                 "source_binding_generation": generation, "local_workspace_id": local.project_workspace_id}
        if runtime.status == "unavailable" and backend == "local":
            extra["kind"] = "recovery"
        reserved = []
        try:
            # Forking a shared environment must capture it under its writer reservation.
            if workspace.kind != "session" and backend != "local":
                await service.switcher.reserve_idle(workspace, runtime, generation, allow_unavailable=runtime.status == "unavailable")
                reserved.append(workspace.id)
                saved = ({"revision": workspace.head_revision} if runtime.status == "unavailable" else
                         await service._checkpoint(project_id, workspace.id, generation, sync_host=False))
                if not saved["revision"]:
                    raise ProjectWorkspaceError("No saved workspace files are available.", "missing_data")
                async with await _session() as session:
                    owned = await register_workspace(session, project, source, kind="session", identity="session:" + thread_id)
                    await session.commit()
                _, fork, fork_runtime = await service.rows(project_id, owned.project_workspace_id)
                service.switcher.assert_available(fork.id, fork_runtime)
                if fork_runtime.status == "retired" or source_key(WorkspaceRef.model_validate_json(fork_runtime.execution_json)) != source_key(source):
                    async with await _session() as session:
                        await session.execute(update(WorkspaceRuntime).where(WorkspaceRuntime.id == fork_runtime.id).values(
                            execution_json=source.model_dump_json(), status="ready", generation=fork_runtime.generation + 1,
                            pending_decision_json="{}"))
                        await session.commit()
                    owned = await service.binding(project_id, owned.project_workspace_id)
                head = await self.copy_revision(workspace.id, owned.project_workspace_id, saved["revision"])
                baseline = await self.copy_revision(workspace.id, owned.project_workspace_id,
                                                    head if source.backend == "local" else workspace.local_sync_revision)
                async with await _session() as session:
                    await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == owned.project_workspace_id).values(
                        head_revision=head, local_sync_revision=baseline, persistence_status="clean", source_json=workspace.source_json))
                    await session.commit()
                extra["reserved_source_id"] = workspace.id
                workspace_id, generation = owned.project_workspace_id, owned.runtime_generation
            if backend == "local":
                _, target, target_runtime = await service.rows(project_id, local.project_workspace_id)
                await service.switcher.reserve_idle(target, target_runtime, target_runtime.generation)
                reserved.append(target.id)
                extra["reserved_local_id"] = target.id
            result = await service.switcher.request(project_id, backend, generation, workspace_id,
                                                    preview_token=preview_token, session_state=extra)
            reserved.clear()  # The durable transition owns these reservations now.
            return result
        finally:
            for key in reserved:
                service.switcher._release(key)

    async def publish(self, project, workspace, ref, generation, transition):
        from agent.modules.agent_runtime import BackgroundTaskRecord
        from agent.modules.workspaces import ThreadWorkspace
        thread = transition["thread_id"]
        source_id = transition["source_workspace_id"]
        source_binding = await self.service.resolve_thread(thread)
        if not source_binding or source_binding.project_workspace_id != source_id or source_binding.runtime_generation != transition["source_binding_generation"]:
            raise ProjectWorkspaceError("The conversation changed during the backend switch.", "stale_runtime")
        local_target = ref.backend == "local"
        retired_source = False
        target = await self.service.binding(project.id, transition["local_workspace_id"]) if local_target else None
        async with await _session() as session:
            current = (await session.execute(select(WorkspaceRuntime).where(WorkspaceRuntime.project_workspace_id == workspace.id))).scalar_one()
            if current.generation != generation - 1:
                raise ProjectWorkspaceError("Runtime publication belongs to an obsolete generation.", "stale_runtime")
            if not local_target:
                aliases = set(json.loads(workspace.runtime_aliases_json))
                # Shared source paths must continue resolving to their original workspace.
                if workspace.id == source_id:
                    aliases.add(source_key(WorkspaceRef.model_validate_json(current.execution_json)))
                await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace.id).values(
                    runtime_aliases_json=json.dumps(sorted(aliases))))
            current.execution_json = current.execution_json if local_target else ref.model_dump_json()
            if not local_target or workspace.kind == "session":
                current.generation, current.status = generation, "retired" if local_target else "ready"
            current.transition_json = json.dumps(transition)
            if not local_target or workspace.kind == "session":
                current.pending_decision_json = "{}"
            current.updated_at = utcnow()
            execution = target.execution if target else ref
            target_id = target.project_workspace_id if target else workspace.id
            for model in (ThreadWorkspace, BackgroundTaskRecord):
                await session.execute(update(model).where(model.project_workspace_id == source_id, or_(
                    model.thread_id == thread, model.thread_id.startswith(thread + ":sub:", autoescape=True),
                )).values(project_workspace_id=target_id, working_dir=execution.locator,
                          execution_backend=execution.backend, execution_locator=execution.locator,
                          execution_label=execution.label, execution_metadata_json=json.dumps(execution.metadata), updated_at=utcnow()))
            source_ref = WorkspaceRef.model_validate(transition["source"])
            if source_ref.backend != "local" and (source_id != workspace.id or local_target):
                retained = False
                for model in (ThreadWorkspace, BackgroundTaskRecord):
                    query = select(model.id).where(or_(model.project_workspace_id == source_id,
                        (model.execution_backend == source_ref.backend) & (model.execution_locator == source_ref.locator)))
                    if model is BackgroundTaskRecord:
                        query = query.where(model.deleted_at.is_(None))
                    retained = retained or (await session.execute(query.limit(1))).scalar_one_or_none() is not None
                if not retained:
                    await session.execute(update(WorkspaceRuntime).where(WorkspaceRuntime.project_workspace_id == source_id).values(status="retired"))
                    retired_source = True
            await session.commit()
        if local_target:
            self.service.active.discard(workspace.id)
        if retired_source:
            self.service.active.discard(source_id)
        self.service._notify_runtime_changed(project.id, source_id)
        self.service._notify_runtime_changed(project.id, target_id)

    async def pending(self, thread_id):
        from agent.shared.thread_ids import resolve_thread_id
        thread = resolve_thread_id(thread_id).split(":sub:", 1)[0]
        async with await _session() as session:
            rows = (await session.execute(select(WorkspaceRuntime).join(
                ProjectWorkspace, ProjectWorkspace.id == WorkspaceRuntime.project_workspace_id,
            ).where(ProjectWorkspace.identity_key == "session:" + thread))).scalars().all()
        from agent.modules.projects.runtime_switch import ACTIVE_PHASES, transition_state
        binding = await self.service.resolve_thread(thread)
        return next((row for row in rows if (state := transition_state(row))
                     and state.get("thread_id") == thread and (state.get("phase") in ACTIVE_PHASES or (
                         state.get("phase") == "failed" and binding and binding.project_workspace_id == state.get("source_workspace_id")
                         and binding.runtime_generation == state.get("source_binding_generation")))), None)
