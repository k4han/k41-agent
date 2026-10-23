"""Project-owned workspace files and replaceable execution lifecycles."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from sqlalchemy import JSON, and_, cast, func, or_, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agent.modules.projects.models import Project, ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.storage import LocalProjectStorage, file_chunks
from agent.modules.projects.transfer import WorkspaceSnapshotTransfer
from agent.modules.workspaces import WorkspaceUnavailableError
from agent.modules.workspaces import WorkspaceBinding, WorkspaceRef, WorkspaceScope, bind_workspace_ref
from agent.shared.infrastructure.db.base import utcnow
from agent.modules.tools import CodingError

logger = logging.getLogger(__name__)


class ProjectWorkspaceError(CodingError, RuntimeError):
    def __init__(self, message: str, code: str = "persistence_error"):
        CodingError.__init__(self, code, message)


async def _session():
    from agent.modules.projects.service import get_async_session
    return await get_async_session()


def source_key(ref: WorkspaceRef) -> str:
    return hashlib.sha256(json.dumps([ref.backend, ref.locator, ref.metadata.get("root", "")]).encode()).hexdigest()


def storage_timestamp(value) -> str | None:
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def database_storage_namespace(database_url: str) -> str:
    """Password rotation must not change the durable files' location."""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        database = url.database if url.database in {None, ":memory:"} else os.path.normcase(str(Path(url.database).resolve()))
        identity = ["sqlite", database]
    else:
        identity = [url.get_backend_name(), url.host, url.port or 5432, url.database, url.username, url.query.get("options", "")]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]


def _insert(session, model):
    return (pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert)(model)


def _scope(project: Project) -> WorkspaceScope:
    payload = json.loads(project.workspace_json)
    scope = WorkspaceScope.model_validate(payload["scope"])
    return scope.model_copy(update={"key": project.key})


def _binding(project: Project, workspace: ProjectWorkspace, runtime: WorkspaceRuntime) -> WorkspaceBinding:
    ref = WorkspaceRef.model_validate_json(runtime.execution_json)
    from agent.modules.skills import skill_workspace_key as workspace_key
    source = WorkspaceRef.model_validate_json(workspace.source_json)
    ref.metadata["project_skill_workspace_key"] = workspace_key(source)
    ref.metadata["project_skill_root"] = source.locator if source.backend == "local" else str(source.metadata.get("root", ""))
    ref.metadata["project_scope"] = _scope(project).model_dump()
    return bind_workspace_ref(ref, scope=_scope(project),
                              project_id=project.id, project_workspace_id=workspace.id,
                              runtime_generation=runtime.generation)


async def register_workspace(session, project: Project, ref: WorkspaceRef, *, kind: str = "shared",
                             identity: str | None = None) -> WorkspaceBinding:
    if kind == "github_task" and identity is None:
        candidates = (await session.execute(select(ProjectWorkspace, WorkspaceRuntime).join(
            WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id,
        ).where(ProjectWorkspace.project_id == project.id, ProjectWorkspace.kind == "github_task"))).all()
        key = source_key(ref)
        for owned, current in candidates:
            if key in {source_key(WorkspaceRef.model_validate_json(current.execution_json)),
                       source_key(WorkspaceRef.model_validate_json(owned.source_json)), *json.loads(owned.runtime_aliases_json)}:
                return _binding(project, owned, current)
    identity = identity or ("shared" if kind == "shared" else kind + ":" + source_key(ref))
    now = utcnow()
    await session.execute(_insert(session, ProjectWorkspace).values(
        project_id=project.id, identity_key=identity, kind=kind, source_json=ref.model_dump_json(),
        runtime_aliases_json="[]",
        persistence_status="dirty", last_error="", created_at=now, updated_at=now,
    ).on_conflict_do_nothing(index_elements=["project_id", "identity_key"]))
    workspace = (await session.execute(select(ProjectWorkspace).where(
        ProjectWorkspace.project_id == project.id, ProjectWorkspace.identity_key == identity,
    ))).scalar_one()
    await session.execute(_insert(session, WorkspaceRuntime).values(
        project_workspace_id=workspace.id, execution_json=ref.model_dump_json(), generation=1,
        status="ready", last_used_at=now, updated_at=now,
    ).on_conflict_do_nothing(index_elements=["project_workspace_id"]))
    runtime = (await session.execute(select(WorkspaceRuntime).where(
        WorkspaceRuntime.project_workspace_id == workspace.id,
    ))).scalar_one()
    if kind == "shared" and source_key(ref) not in {
        source_key(WorkspaceRef.model_validate_json(workspace.source_json)),
        source_key(WorkspaceRef.model_validate_json(runtime.execution_json)), *json.loads(workspace.runtime_aliases_json),
    }:
        raise ProjectWorkspaceError("A different workspace is already registered for this project.", "workspace_conflict")
    return _binding(project, workspace, runtime)


class ProjectWorkspaceService:
    def __init__(self, container, *, storage=None, transfer=None):
        self.container = container
        configured = container.config_service.get("workspace.project_storage_root", None)
        root = Path(configured).expanduser() if configured else Path.home() / ".k41-agent" / "project-storage"
        namespace = database_storage_namespace(container.database_url)
        self.storage = storage or LocalProjectStorage(root / namespace)
        self.runtime_root = root.parent / "project-runtimes" / namespace
        self.transfer = transfer or WorkspaceSnapshotTransfer()
        self.locks: dict[int, asyncio.Lock] = {}
        self.migration_locks: dict[int, asyncio.Lock] = {}
        self.selection_locks: dict[int, asyncio.Lock] = {}
        self.active: set[int] = set()
        self.checkpoint_requested = asyncio.Event()
        self.running_processes: set[tuple[int, str]] = set()
        self.periodic_task = None
        self.closing = False

    def lock(self, workspace_id: int) -> asyncio.Lock:
        return self.locks.setdefault(workspace_id, asyncio.Lock())

    def monitor(self, workspace_id: int) -> None:
        self.active.add(workspace_id)
        self.checkpoint_requested.set()

    async def rows(self, project_id: int, workspace_id: int | None = None):
        session = await _session()
        async with session:
            project = await session.get(Project, project_id)
            if project is None:
                raise KeyError("Project not found.")
            statement = select(ProjectWorkspace).where(ProjectWorkspace.project_id == project_id)
            statement = statement.where(ProjectWorkspace.id == workspace_id) if workspace_id else statement.where(ProjectWorkspace.identity_key == "shared")
            workspace = (await session.execute(statement)).scalar_one_or_none()
            if workspace is None:
                raise ProjectWorkspaceError("Project workspace has not been migrated.", "migration_required")
            runtime = (await session.execute(select(WorkspaceRuntime).where(
                WorkspaceRuntime.project_workspace_id == workspace.id,
            ))).scalar_one()
            return project, workspace, runtime

    async def describe(self, project_id: int) -> dict:
        descriptions = await self.describe_many([project_id])
        if project_id not in descriptions:
            raise KeyError("Project not found.")
        return descriptions[project_id]

    async def describe_many(self, project_ids: list[int]) -> dict[int, dict]:
        if not project_ids:
            return {}
        session = await _session()
        async with session:
            projects = (await session.execute(select(Project).where(Project.id.in_(project_ids)))).scalars().all()
            rows = (await session.execute(select(ProjectWorkspace, WorkspaceRuntime).join(
                WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id,
            ).where(ProjectWorkspace.project_id.in_(project_ids)).order_by(ProjectWorkspace.id))).all()
        grouped = {project.id: [] for project in projects}
        for workspace, runtime in rows:
            if workspace.project_id in grouped:
                grouped[workspace.project_id].append((workspace, runtime))
        descriptions = {}
        for project in projects:
            rows = grouped[project.id]
            workspaces = [{"id": workspace.id, "kind": workspace.kind,
                           "source": json.loads(workspace.source_json),
                           "runtime": {"status": runtime.status, "generation": runtime.generation},
                           "persistence": {"status": workspace.persistence_status, "revision": workspace.head_revision,
                                           "last_saved_at": storage_timestamp(workspace.last_saved_at),
                                           "last_error": workspace.last_error,
                                           "blocked_for_writes": workspace.persistence_status == "error"}}
                          for workspace, runtime in rows]
            shared = next(((workspace, runtime) for workspace, runtime in rows if workspace.identity_key == "shared"), None)
            result = {"migration_status": project.migration_status, "workspaces": workspaces}
            if shared:
                result["workspace"] = _binding(project, *shared).model_dump()
                summary = next(item for item in workspaces if item["id"] == shared[0].id)
                result.update(runtime=summary["runtime"], persistence=summary["persistence"])
            descriptions[project.id] = result
        return descriptions

    async def runtime_records(self, backend: str) -> list[dict]:
        session = await _session()
        async with session:
            rows = (await session.execute(select(Project, ProjectWorkspace, WorkspaceRuntime).join(
                ProjectWorkspace, ProjectWorkspace.project_id == Project.id,
            ).join(WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id))).all()
        records = []
        for project, workspace, runtime in rows:
            ref = WorkspaceRef.model_validate_json(runtime.execution_json)
            if ref.backend != backend:
                continue
            ref.metadata.update(status="started" if runtime.status == "ready" else runtime.status,
                                last_used_at=storage_timestamp(runtime.last_used_at), project_id=project.id,
                                project_workspace_id=workspace.id)
            ref.label = project.label
            records.append({"workspace": ref.model_dump(), "created_at": storage_timestamp(workspace.created_at),
                            "updated_at": storage_timestamp(runtime.updated_at)})
        return records

    async def binding(self, project_id: int, workspace_id: int | None = None) -> WorkspaceBinding:
        return _binding(*await self.rows(project_id, workspace_id))

    async def resolve_thread(self, thread_id: str) -> WorkspaceBinding | None:
        from agent.modules.workspaces import ThreadWorkspace
        from agent.shared.thread_ids import resolve_thread_id
        session = await _session()
        async with session:
            row = (await session.execute(select(ThreadWorkspace).where(
                ThreadWorkspace.thread_id == resolve_thread_id(thread_id),
            ))).scalar_one_or_none()
            if row is None and ":sub:" in thread_id:
                row = (await session.execute(select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id == resolve_thread_id(thread_id).split(":sub:", 1)[0],
                ))).scalar_one_or_none()
            if row is None or row.project_workspace_id is None:
                return None
            project_id, workspace_id = row.project_id, row.project_workspace_id
        return await self.binding(project_id, workspace_id)

    async def find_binding(self, workspace=None, *, thread_id: str | None = None,
                           project_id: int | None = None, workspace_id: int | None = None):
        from agent.modules.workspaces import resolve_workspace_ref
        payload = workspace.model_dump() if isinstance(workspace, WorkspaceBinding) else workspace
        if isinstance(payload, dict) and payload.get("project_id"):
            if project_id and project_id != payload["project_id"]:
                raise ProjectWorkspaceError("Workspace reference conflicts with project_id.", "workspace_conflict")
            if workspace_id and payload.get("project_workspace_id") not in {None, workspace_id}:
                raise ProjectWorkspaceError("Workspace reference conflicts with project_workspace_id.", "workspace_conflict")
            project_id, workspace_id = payload["project_id"], payload.get("project_workspace_id") or workspace_id
        if workspace_id and not project_id:
            raise ProjectWorkspaceError("project_id is required for a project workspace.", "workspace_conflict")
        if self.closing or not self.container._persistence_ready:
            if project_id:
                raise ProjectWorkspaceError("Project storage is temporarily unavailable.")
            return None
        if project_id:
            project, owned, runtime = await self.rows(project_id, workspace_id)
            binding = _binding(project, owned, runtime)
            if isinstance(payload, dict) and payload.get("scope", {}).get("key", binding.scope.key) != binding.scope.key:
                raise ProjectWorkspaceError("Workspace scope conflicts with the project.", "workspace_conflict")
            if isinstance(payload, dict) and payload.get("runtime_generation") not in {None, binding.runtime_generation}:
                raise ProjectWorkspaceError("Execution runtime changed; reload the workspace.", "stale_runtime")
            if workspace is not None:
                requested = resolve_workspace_ref(workspace)
                if source_key(requested) not in {source_key(binding.execution), source_key(WorkspaceRef.model_validate_json(owned.source_json)),
                                                *json.loads(owned.runtime_aliases_json)}:
                    raise ProjectWorkspaceError("Workspace reference conflicts with the project.", "workspace_conflict")
            if project.migration_status == "ready" and owned.kind == "legacy":
                return await self.binding(project_id)
            return binding
        if thread_id and workspace is None:
            binding = await self.resolve_thread(thread_id)
            if binding:
                return binding
        if workspace is None:
            return None
        requested = resolve_workspace_ref(workspace)
        session = await _session()
        async with session:
            def matches(column):
                def field(*path):
                    if session.get_bind().dialect.name == "postgresql":
                        value = cast(column, JSON)
                        for name in path:
                            value = value[name]
                        return value.as_string()
                    return func.json_extract(column, "$." + ".".join(path))
                return and_(field("backend") == requested.backend, field("locator") == requested.locator,
                            func.coalesce(field("metadata", "root"), "") == requested.metadata.get("root", ""))
            key = source_key(requested)
            rows = (await session.execute(select(Project, ProjectWorkspace, WorkspaceRuntime).join(
                ProjectWorkspace, ProjectWorkspace.project_id == Project.id,
            ).join(WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id).where(or_(
                matches(ProjectWorkspace.source_json), matches(WorkspaceRuntime.execution_json),
                ProjectWorkspace.runtime_aliases_json.contains('"' + key + '"'),
            )))).all()
            for project, owned, runtime in rows:
                if key in {*json.loads(owned.runtime_aliases_json), source_key(WorkspaceRef.model_validate_json(owned.source_json)),
                           source_key(WorkspaceRef.model_validate_json(runtime.execution_json))}:
                    if project.migration_status == "ready" and owned.kind == "legacy":
                        return await self.binding(project.id)
                    return _binding(project, owned, runtime)
        return None

    async def resolve_context(self, context, *, allow_unready: bool = False):
        binding = None
        if context.project_workspace_id:
            binding = await self.binding(context.project_id, context.project_workspace_id)
            if context.runtime_generation is not None and context.runtime_generation != binding.runtime_generation:
                raise ProjectWorkspaceError("Execution runtime changed; prepare the operation again.", "stale_runtime")
        elif context.thread_id and self.container._persistence_ready:
            binding = await self.resolve_thread(context.thread_id)
        if binding is None:
            return context
        project, _, _ = await self.rows(binding.project_id, binding.project_workspace_id)
        if not allow_unready or project.migration_status == "ready":
            binding = await self.ensure(binding.project_id, binding.project_workspace_id)
        ref = binding.execution
        return replace(context, project_id=binding.project_id, project_workspace_id=binding.project_workspace_id,
                       runtime_generation=binding.runtime_generation, backend=ref.backend, locator=ref.locator,
                       workspace=ref.locator if ref.backend == "local" else str(ref.metadata["root"]))

    async def assert_writable(self, context):
        if not context.project_workspace_id:
            return
        project, workspace, runtime = await self.rows(context.project_id, context.project_workspace_id)
        if self.closing:
            raise ProjectWorkspaceError("The application is shutting down.", "workspace_closing")
        if project.migration_status != "ready":
            raise ProjectWorkspaceError("Choose the main workspace before writing.", "migration_required")
        if workspace.persistence_status == "error":
            raise ProjectWorkspaceError("Workspace has unsaved changes; retry checkpoint before writing.", "writes_blocked")
        if runtime.generation != context.runtime_generation:
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        if runtime.status != "ready":
            raise ProjectWorkspaceError("Restore the execution workspace before writing.", "runtime_unavailable")

    async def _set_error(self, workspace_id: int, error: BaseException):
        session = await _session()
        async with session:
            await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace_id).values(
                persistence_status="error", last_error=str(error)[:2000], updated_at=utcnow(),
            ))
            await session.commit()

    async def _checkpoint(self, project_id: int, workspace_id: int, generation: int | None = None,
                          *, skip_unchanged: bool = False) -> dict:
        project, workspace, runtime = await self.rows(project_id, workspace_id)
        if generation is not None and generation != runtime.generation:
            raise ProjectWorkspaceError("Checkpoint belongs to an obsolete runtime.", "stale_runtime")
        if runtime.status == "restoring":
            raise ProjectWorkspaceError("Restore the workspace before saving its files.", "runtime_unavailable")
        revision, current_generation = workspace.head_revision, runtime.generation
        committed = False
        try:
            ref = WorkspaceRef.model_validate_json(runtime.execution_json)
            manifest = await self.transfer.capture(ref, self.storage, str(workspace.id))
            saved = await asyncio.to_thread(self.storage.put_manifest, str(workspace.id), manifest)
            if skip_unchanged and saved == revision and workspace.persistence_status == "clean":
                return {"status": "clean", "revision": saved, "saved": True}
            session = await _session()
            async with session:
                runtime_check = select(WorkspaceRuntime.id).where(
                    WorkspaceRuntime.project_workspace_id == workspace.id, WorkspaceRuntime.generation == current_generation,
                ).exists()
                result = await session.execute(update(ProjectWorkspace).where(
                    ProjectWorkspace.id == workspace.id, ProjectWorkspace.head_revision == revision, runtime_check,
                ).values(head_revision=saved, previous_revision=revision if saved != revision else workspace.previous_revision,
                         persistence_status="clean", last_error="", last_saved_at=utcnow(), updated_at=utcnow()))
                if result.rowcount != 1:
                    raise ProjectWorkspaceError("Workspace changed while saving.", "stale_runtime")
                await session.commit()
            committed = True
            keep = {value for value in (saved, revision, workspace.previous_revision if saved == revision else None,
                                        workspace.pinned_revision) if value}
            try:
                # A cancelled thread keeps running; retain the lock until collection ends.
                collection = asyncio.create_task(asyncio.to_thread(self.storage.collect, str(workspace.id), keep))
                interrupted = None
                while not collection.done():
                    try:
                        await asyncio.shield(collection)
                    except asyncio.CancelledError as exc:
                        interrupted = exc
                    except Exception:
                        break
                if interrupted is not None:
                    if not collection.cancelled():
                        collection.exception()
                    raise interrupted
                collection.result()
            except Exception:
                logger.warning("Could not collect project storage workspace=%s", workspace.id, exc_info=True)
            return {"status": "clean", "revision": saved, "saved": True}
        except asyncio.CancelledError:
            if not committed:
                await asyncio.shield(self._set_error(workspace.id, ProjectWorkspaceError("Checkpoint was interrupted.")))
            raise
        except Exception as exc:
            if getattr(exc, "code", "") != "stale_runtime":
                await self._set_error(workspace.id, exc)
            raise ProjectWorkspaceError(str(exc), getattr(exc, "code", "persistence_error")) from exc

    async def checkpoint(self, project_id: int, workspace_id: int | None = None, *, generation: int | None = None,
                         skip_unchanged: bool = False):
        _, workspace, _ = await self.rows(project_id, workspace_id)
        async with self.lock(workspace.id):
            return await self._checkpoint(project_id, workspace.id, generation, skip_unchanged=skip_unchanged)

    async def ensure(self, project_id: int, workspace_id: int | None = None) -> WorkspaceBinding:
        session = await _session()
        async with session:
            project = await session.get(Project, project_id)
            if project is None:
                raise KeyError("Project not found.")
            pending = project.migration_status != "ready"
        if pending:
            await self.migrate(project_id)
        try:
            project, workspace, runtime = await self.rows(project_id, workspace_id)
        except ProjectWorkspaceError as exc:
            if workspace_id or exc.code != "migration_required":
                raise
            await self._initialize_shared(project_id)
            project, workspace, runtime = await self.rows(project_id)
        if project.migration_status != "ready":
            raise ProjectWorkspaceError("Choose the main workspace before continuing.", "migration_required")
        async with self.lock(workspace.id):
            project, workspace, runtime = await self.rows(project_id, workspace.id)
            ref = WorkspaceRef.model_validate_json(runtime.execution_json)
            if runtime.status != "restoring":
                try:
                    if ref.backend == "local":
                        if not Path(ref.locator).is_dir():
                            if workspace.kind == "github_task" and workspace.head_revision:
                                destination = self.runtime_root / str(workspace.id) / f"{runtime.generation + 1}-{uuid4().hex}"
                                try:
                                    await asyncio.to_thread(self.transfer.materialize, self.storage, str(workspace.id),
                                                            workspace.head_revision, destination)
                                except Exception as exc:
                                    await self._set_error(workspace.id, exc)
                                    raise ProjectWorkspaceError("Task restore failed: " + str(exc)) from exc
                                replacement = ref.model_copy(update={"locator": str(destination)})
                                await self._publish_runtime(project, workspace, replacement, runtime.generation + 1, "ready")
                                session = await _session()
                                async with session:
                                    await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace.id).values(
                                        persistence_status="clean", last_error=""))
                                    await session.commit()
                                self.active.add(workspace.id)
                                return await self.binding(project_id, workspace.id)
                            raise WorkspaceUnavailableError("Registered local workspace is unavailable.")
                        ready = ref
                    else:
                        from agent.modules.workspaces import attach_workspace_backend, merge_ready_workspace_metadata as _merge_ready_workspace_metadata
                        attached = await attach_workspace_backend(ref.backend, ref.locator, label=ref.label,
                                                                  root=ref.metadata.get("root"))
                        ready = _merge_ready_workspace_metadata(ref, attached)
                    if not workspace.head_revision:
                        await self._checkpoint(project_id, workspace.id, runtime.generation)
                    elif workspace.persistence_status == "dirty":
                        try:
                            await self._checkpoint(project_id, workspace.id, runtime.generation)
                        except ProjectWorkspaceError:
                            logger.warning("Recovered runtime has unsaved changes workspace=%s", workspace.id)
                    await self._publish_runtime(project, workspace, ready, runtime.generation, "ready")
                    self.active.add(workspace.id)
                    return await self.binding(project_id, workspace.id)
                except Exception as exc:
                    from agent.modules.workspaces import is_workspace_unavailable_error
                    unavailable = is_workspace_unavailable_error(ref, exc)
                    if not unavailable or ref.backend == "local":
                        raise
            if not workspace.head_revision:
                raise ProjectWorkspaceError("Both the runtime and saved project files are unavailable.", "missing_data")
            from agent.modules.workspaces import create_workspace_backend, merge_ready_workspace_metadata as _merge_ready_workspace_metadata
            replacement = await create_workspace_backend(ref.backend, label=ref.label)
            replacement = _merge_ready_workspace_metadata(ref, replacement)
            if ref.metadata.get("root"):
                replacement.metadata["root"] = ref.metadata["root"]
            generation = runtime.generation + 1
            await self._publish_runtime(project, workspace, replacement, generation, "restoring")
            try:
                await self.transfer.restore(replacement, self.storage, str(workspace.id), workspace.head_revision)
            except Exception as exc:
                await self._set_error(workspace.id, exc)
                try:
                    from agent.modules.workspaces import get_workspace_backend_registry, call_workspace_backend_loader
                    descriptor = get_workspace_backend_registry().require(replacement.backend)
                    if descriptor.delete_loader:
                        await call_workspace_backend_loader(replacement.backend, descriptor.delete_loader, replacement,
                                                            in_thread=replacement.backend == "daytona")
                except Exception:
                    logger.warning("Incomplete runtime retained for cleanup workspace=%s", workspace.id)
                raise ProjectWorkspaceError("Project restore failed: " + str(exc)) from exc
            await self._publish_runtime(project, workspace, replacement, generation, "ready")
            session = await _session()
            async with session:
                await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace.id).values(
                    persistence_status="clean", last_error=""))
                await session.commit()
            self.active.add(workspace.id)
            return await self.binding(project_id, workspace.id)

    async def _initialize_shared(self, project_id: int):
        async with self.migration_locks.setdefault(project_id, asyncio.Lock()):
            session = await _session()
            async with session:
                project = await session.get(Project, project_id)
                existing = (await session.execute(select(ProjectWorkspace.id).where(
                    ProjectWorkspace.project_id == project_id, ProjectWorkspace.identity_key == "shared",
                ))).scalar_one_or_none()
                if existing:
                    return
                if project.migration_status != "ready":
                    raise ProjectWorkspaceError("Choose the main workspace before continuing.", "migration_required")
                source = WorkspaceRef.model_validate(json.loads(project.workspace_json)["execution"])
            repository_id = source.metadata.get("repository_id")
            if not repository_id and source.metadata.get("repository_full_name"):
                from agent.modules.github import get_github_repository_store
                repository = await get_github_repository_store().get_binding_by_full_name(source.metadata["repository_full_name"])
                repository_id = repository.repository_id if repository else None
            if source.metadata.get("source") != "github" or not repository_id:
                raise ProjectWorkspaceError("A shared project workspace must be selected.", "migration_required")
            from agent.modules.workspaces import create_workspace_backend
            from agent.modules.workspaces import attach_github_repository_to_workspace
            target = source if source.backend == "local" else await create_workspace_backend(source.backend, label=source.label)
            ref = await attach_github_repository_to_workspace(target, repository_id=int(repository_id))
            session = await _session()
            async with session:
                project = await session.get(Project, project_id)
                await register_workspace(session, project, ref)
                await session.commit()

    async def _publish_runtime(self, project, workspace, ref, generation, status):
        session = await _session()
        async with session:
            previous = (await session.execute(select(WorkspaceRuntime).where(
                WorkspaceRuntime.project_workspace_id == workspace.id,
            ))).scalar_one()
            previous_key = source_key(WorkspaceRef.model_validate_json(previous.execution_json))
            if generation < previous.generation or (previous_key != source_key(ref) and generation != previous.generation + 1):
                raise ProjectWorkspaceError("Runtime publication belongs to an obsolete generation.", "stale_runtime")
            if previous_key != source_key(ref):
                current = await session.get(ProjectWorkspace, workspace.id)
                aliases = set(json.loads(current.runtime_aliases_json))
                aliases.add(previous_key)
                current.runtime_aliases_json = json.dumps(sorted(aliases))
            changed = await session.execute(update(WorkspaceRuntime).where(WorkspaceRuntime.project_workspace_id == workspace.id,
                WorkspaceRuntime.generation == previous.generation).values(
                execution_json=ref.model_dump_json(), generation=generation, status=status, last_used_at=utcnow(), updated_at=utcnow(),
            ))
            if changed.rowcount != 1:
                raise ProjectWorkspaceError("Runtime changed during publication.", "stale_runtime")
            if workspace.kind == "shared":
                binding = bind_workspace_ref(ref, scope=_scope(project), project_id=project.id,
                                             project_workspace_id=workspace.id, runtime_generation=generation)
                await session.execute(update(Project).where(Project.id == project.id).values(workspace_json=binding.model_dump_json()))
            await session.commit()

    @asynccontextmanager
    async def mutation(self, project_id: int, workspace_id: int, *, generation: int | None = None):
        async with self.lock(workspace_id):
            project, workspace, runtime = await self.rows(project_id, workspace_id)
            if self.closing:
                raise ProjectWorkspaceError("The application is shutting down.", "workspace_closing")
            if project.migration_status != "ready":
                raise ProjectWorkspaceError("Choose the main workspace before writing.", "migration_required")
            if workspace.persistence_status == "error":
                raise ProjectWorkspaceError("Workspace has unsaved changes; retry checkpoint before writing.", "writes_blocked")
            if generation is not None and runtime.generation != generation:
                raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
            if runtime.status != "ready":
                raise ProjectWorkspaceError("Restore the execution workspace before writing.", "runtime_unavailable")
            session = await _session()
            async with session:
                await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace_id).values(
                    persistence_status="dirty", updated_at=utcnow()))
                await session.commit()
            self.active.add(workspace_id)
            yield

    async def persist_result(self, context, result):
        if not context.project_workspace_id:
            return result
        try:
            result.data["persistence"] = await self._checkpoint(context.project_id, context.project_workspace_id,
                                                               context.runtime_generation)
        except ProjectWorkspaceError as exc:
            result.data["persistence"] = {"status": "error", "saved": False, "message": str(exc)}
            result.warnings.append("Execution finished, but project changes have not been saved: " + str(exc))
            if isinstance(result.content, str):
                result.content += "\n[warning] Project changes have not been saved. Retry checkpoint before writing."
        return result

    async def migrate(self, project_id: int):
        from agent.modules.workspaces import ThreadWorkspace
        from agent.modules.agent_runtime import BackgroundTaskRecord
        from agent.modules.workspaces import workspace_ref_from_columns
        async with self.migration_locks.setdefault(project_id, asyncio.Lock()):
            session = await _session()
            async with session:
                project = await session.get(Project, project_id)
                if project.migration_status == "ready":
                    return
                initial = WorkspaceRef.model_validate(json.loads(project.workspace_json)["execution"])
                def task_source(ref):
                    return ref.metadata.get("workspace_kind") == "github_task" or (
                        ref.metadata.get("source") == "github" and ref.metadata.get("branch") and ref.metadata.get("base_branch"))
                sources = {} if task_source(initial) else {source_key(initial): initial}
                records = []
                for model in (ThreadWorkspace, BackgroundTaskRecord):
                    records.extend((await session.execute(select(model).where(model.scope_key == project.key))).scalars())
                for record in records:
                    if not record.execution_locator:
                        continue
                    ref = workspace_ref_from_columns(backend=record.execution_backend, locator=record.execution_locator,
                                                     label=record.execution_label, metadata_json=record.execution_metadata_json)
                    kind = "github_task" if task_source(ref) else "legacy"
                    binding = await register_workspace(session, project, ref, kind=kind)
                    record.project_id, record.project_workspace_id = project_id, binding.project_workspace_id
                    if kind == "legacy":
                        sources[source_key(ref)] = ref
                for ref in sources.values():
                    await register_workspace(session, project, ref, kind="legacy")
                project.migration_status = "capturing"
                await session.commit()
            session = await _session()
            async with session:
                workspaces = list((await session.execute(select(ProjectWorkspace).where(
                    ProjectWorkspace.project_id == project_id,
                ))).scalars())
            for workspace in workspaces:
                if workspace.head_revision:
                    continue
                try:
                    await self.checkpoint(project_id, workspace.id)
                    session = await _session()
                    async with session:
                        current = await session.get(ProjectWorkspace, workspace.id)
                        current.pinned_revision = current.head_revision
                        await session.commit()
                except ProjectWorkspaceError:
                    logger.warning("Legacy workspace unavailable project=%s workspace=%s", project_id, workspace.id)
            session = await _session()
            async with session:
                candidates = list((await session.execute(select(ProjectWorkspace).where(
                    ProjectWorkspace.project_id == project_id, ProjectWorkspace.kind == "legacy",
                ))).scalars())
                revisions = {workspace.head_revision for workspace in candidates}
                project = await session.get(Project, project_id)
                project.migration_status = "needs_selection" if len(candidates) > 1 else "missing_data" if candidates else "ready"
                await session.commit()
            if candidates and len(revisions) == 1 and None not in revisions:
                await self.select_migration_source(project_id, candidates[0].id)
            elif len(candidates) == 1 and candidates[0].head_revision:
                await self.select_migration_source(project_id, candidates[0].id)

    async def select_migration_source(self, project_id: int, source_id: int):
        async with self.selection_locks.setdefault(project_id, asyncio.Lock()):
            return await self._select_migration_source(project_id, source_id)

    async def _select_migration_source(self, project_id: int, source_id: int):
        from agent.modules.workspaces import ThreadWorkspace
        from agent.modules.agent_runtime import BackgroundTaskRecord
        session = await _session()
        async with session:
            project = await session.get(Project, project_id)
            if project is None:
                raise KeyError("Project not found.")
            source = await session.get(ProjectWorkspace, source_id)
            if project.migration_status == "ready":
                if source and source.project_id == project_id and source.kind == "shared":
                    return await self.binding(project_id, source_id)
                raise ProjectWorkspaceError("The main workspace has already been selected.", "workspace_conflict")
            if source is None or source.project_id != project_id or source.kind != "legacy" or not source.head_revision:
                raise ProjectWorkspaceError("Choose an available migration source.", "invalid_source")
            await asyncio.to_thread(self.storage.get_manifest, str(source.id), source.head_revision)
            source.kind, source.identity_key = "shared", "shared"
            project.migration_status = "ready"
            await session.flush()
            for model in (ThreadWorkspace, BackgroundTaskRecord):
                old_ids = select(ProjectWorkspace.id).where(ProjectWorkspace.project_id == project_id,
                                                           ProjectWorkspace.kind == "legacy")
                await session.execute(update(model).where(model.project_id == project_id,
                    model.project_workspace_id.in_(old_ids)).values(project_workspace_id=source.id))
            await session.commit()
        binding = await self.binding(project_id, source_id)
        project, workspace, runtime = await self.rows(project_id, source_id)
        await self._publish_runtime(project, workspace, binding.execution, runtime.generation, runtime.status)
        return binding

    async def read_saved(self, project_id: int, path: str, workspace_id: int | None = None, *, max_bytes: int | None = None) -> tuple[bytes, dict]:
        _, workspace, _ = await self.rows(project_id, workspace_id)
        async with self.lock(workspace.id):
            _, workspace, _ = await self.rows(project_id, workspace.id)
            if not workspace.head_revision:
                raise WorkspaceUnavailableError("No saved project files are available.")
            manifest = await asyncio.to_thread(self.storage.get_manifest, str(workspace.id), workspace.head_revision)
            entry = manifest["entries"].get(path)
            if not entry or entry["type"] != "file":
                raise FileNotFoundError(path)
            if max_bytes is not None and entry["size"] > max_bytes:
                raise ValueError("Project file exceeds the read limit.")
            content = bytearray()
            for chunk in file_chunks(entry):
                content.extend(await asyncio.to_thread(self.storage.get_blob, str(workspace.id), chunk["checksum"]))
            return bytes(content), {"saved_revision": workspace.head_revision,
                                    "last_saved_at": storage_timestamp(workspace.last_saved_at)}

    async def retire_matching(self, ref: WorkspaceRef, action: str) -> str | None:
        if not self.container._persistence_ready:
            return None
        await self.capture_pending()
        session = await _session()
        async with session:
            rows = (await session.execute(select(ProjectWorkspace, WorkspaceRuntime).join(
                WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id,
            ))).all()
        matches = [(workspace, runtime) for workspace, runtime in rows
                   if (value := WorkspaceRef.model_validate_json(runtime.execution_json)).backend == ref.backend
                   and value.locator == ref.locator]
        if not matches:
            return None
        from agent.modules.workspaces import get_workspace_backend_registry, call_workspace_backend_loader
        descriptor = get_workspace_backend_registry().require(ref.backend)
        loader = getattr(descriptor, action + "_loader")
        if not loader:
            raise ValueError("Unsupported project runtime lifecycle operation.")
        # A provider sandbox can contain more than one legacy root. Protect every one.
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            for workspace, runtime in sorted(matches, key=lambda pair: pair[0].id):
                await stack.enter_async_context(self.lock(workspace.id))
                coding = self.container._coding_service
                if coding:
                    actual = WorkspaceRef.model_validate_json(runtime.execution_json)
                    for client in tuple(coding.remote.clients.values()):
                        if client.backend.ref.locator == actual.locator and client.backend.ref.backend == actual.backend:
                            await client.call({"operation": "stop_workspace", "workspace": client.backend.root})
                await self._checkpoint(workspace.project_id, workspace.id, runtime.generation)
            result = await call_workspace_backend_loader(ref.backend, loader, ref, in_thread=ref.backend == "daytona")
            session = await _session()
            async with session:
                for workspace, _ in matches:
                    await session.execute(update(WorkspaceRuntime).where(WorkspaceRuntime.project_workspace_id == workspace.id).values(
                        status={"stop": "stopped", "archive": "archived", "delete": "destroyed"}[action], updated_at=utcnow()))
                    self.active.discard(workspace.id)
                await session.commit()
            return result

    async def sweep_idle_daytona(self, *, now, stop_after, archive_after) -> dict:
        from datetime import timezone
        counts = {"checked": 0, "stopped": 0, "archived": 0, "skipped": 0, "errors": []}
        if not self.container._persistence_ready:
            return counts
        session = await _session()
        async with session:
            rows = (await session.execute(select(WorkspaceRuntime))).scalars().all()
        for runtime in rows:
            ref = WorkspaceRef.model_validate_json(runtime.execution_json)
            if ref.backend != "daytona":
                continue
            counts["checked"] += 1
            if runtime.status in {"destroyed", "archived", "restoring"}:
                counts["skipped"] += 1
                continue
            workspace_id = runtime.project_workspace_id
            if self.lock(workspace_id).locked() or any(value[0] == workspace_id for value in self.running_processes):
                counts["skipped"] += 1
                continue
            last_used = runtime.last_used_at.replace(tzinfo=timezone.utc) if runtime.last_used_at.tzinfo is None else runtime.last_used_at
            idle = now - last_used
            action = "archive" if archive_after and idle >= archive_after and runtime.status != "archived" else (
                "stop" if stop_after and idle >= stop_after and runtime.status == "ready" else None)
            if action:
                try:
                    await self.retire_matching(ref, action)
                    counts["archived" if action == "archive" else "stopped"] += 1
                except Exception as exc:
                    counts["errors"].append({"workspace_id": workspace_id, "error": str(exc)})
        return counts

    @asynccontextmanager
    async def saved_view(self, project_id: int, workspace_id: int | None = None):
        _, workspace, _ = await self.rows(project_id, workspace_id)
        if not workspace.head_revision:
            raise WorkspaceUnavailableError("No saved project files are available.")
        with tempfile.TemporaryDirectory(prefix="k41-project-view-") as path:
            async with self.lock(workspace.id):
                _, workspace, _ = await self.rows(project_id, workspace.id)
                await asyncio.to_thread(self.transfer.materialize, self.storage, str(workspace.id), workspace.head_revision, Path(path))
            yield WorkspaceRef(backend="local", locator=path, metadata={
                "saved_revision": workspace.head_revision, "last_saved_at": storage_timestamp(workspace.last_saved_at),
            })

    def start(self):
        self.closing = False
        if self.periodic_task is None:
            self.periodic_task = asyncio.create_task(self._periodic())

    async def _periodic(self):
        try:
            await self.capture_pending()
            session = await _session()
            async with session:
                resumed = (await session.execute(select(ProjectWorkspace.id).join(
                    WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id,
                ).join(Project, Project.id == ProjectWorkspace.project_id).where(
                    Project.migration_status == "ready", ProjectWorkspace.kind != "legacy",
                    WorkspaceRuntime.status == "ready",
                ))).scalars()
                self.active.update(resumed)
        except Exception:
            logger.warning("Project checkpoint initialization deferred.", exc_info=True)
        while True:
            try:
                await asyncio.wait_for(self.checkpoint_requested.wait(), timeout=30)
            except TimeoutError:
                pass
            self.checkpoint_requested.clear()
            for workspace_id in tuple(self.active):
                try:
                    session = await _session()
                    async with session:
                        workspace = await session.get(ProjectWorkspace, workspace_id)
                        runtime = (await session.execute(select(WorkspaceRuntime).where(
                            WorkspaceRuntime.project_workspace_id == workspace_id,
                        ))).scalar_one_or_none()
                    if workspace is None or runtime is None or runtime.status != "ready":
                        self.active.discard(workspace_id)
                        continue
                    if self.lock(workspace_id).locked():
                        continue
                    await self.checkpoint(workspace.project_id, workspace_id, skip_unchanged=True)
                except Exception:
                    logger.warning("Periodic checkpoint failed workspace=%s", workspace_id, exc_info=True)

    async def capture_pending(self):
        session = await _session()
        async with session:
            pending = list((await session.execute(select(Project.id).where(Project.migration_status.in_(["pending", "capturing"])))).scalars())
        for project_id in pending:
            try:
                await self.migrate(project_id)
            except Exception:
                logger.warning("Project migration deferred project=%s", project_id, exc_info=True)

    async def preserved_local_paths(self) -> set[Path]:
        await self.capture_pending()
        session = await _session()
        async with session:
            rows = (await session.execute(select(ProjectWorkspace, WorkspaceRuntime).join(
                WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id,
            ))).all()
        return {Path(ref.locator).resolve() for workspace, runtime in rows
                if (ref := WorkspaceRef.model_validate_json(runtime.execution_json)).backend == "local"
                and (workspace.kind != "github_task" or not workspace.head_revision or workspace.persistence_status != "clean")}

    async def release_task_runtime(self, binding):
        _, workspace, _ = await self.rows(binding.project_id, binding.project_workspace_id)
        if workspace.kind != "github_task":
            return
        session = await _session()
        async with session:
            await session.execute(update(WorkspaceRuntime).where(WorkspaceRuntime.project_workspace_id == workspace.id).values(
                status="destroyed", updated_at=utcnow()))
            await session.commit()
        self.active.discard(workspace.id)

    async def close(self):
        self.closing = True
        if self.periodic_task:
            self.periodic_task.cancel()
            await asyncio.gather(self.periodic_task, return_exceptions=True)
            self.periodic_task = None
        for workspace_id in tuple(self.active):
            try:
                session = await _session()
                async with session:
                    workspace = await session.get(ProjectWorkspace, workspace_id)
                if workspace:
                    await self.checkpoint(workspace.project_id, workspace_id)
            except Exception:
                logger.error("Unsaved workspace retained at shutdown workspace=%s", workspace_id, exc_info=True)


def get_project_workspace_service(container=None) -> ProjectWorkspaceService:
    from agent.bootstrap.container import require_active_container
    active = require_active_container(container)
    with active._lazy_lock:
        if active._project_workspace_service is None:
            active._project_workspace_service = ProjectWorkspaceService(active)
        return active._project_workspace_service
