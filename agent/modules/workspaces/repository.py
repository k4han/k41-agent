from __future__ import annotations

import json
import threading
from secrets import token_hex
from typing import Any

from agent.shared.thread_ids import resolve_thread_id

from sqlalchemy import Engine, create_engine, select

from agent.modules.workspaces.models import ThreadWorkspace
from agent.modules.workspaces.refs import (
    WorkspaceBinding,
    WorkspaceRef,
    bind_workspace_ref,
    binding_from_record,
    normalize_workspace_ref,
    workspace_ref_from_columns,
)
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.engine import (
    _normalize_url_to_sync,
    get_database_url,
)
from agent.shared.infrastructure.db.session import get_async_session
from agent.shared.config.constants import DEFAULT_WORKSPACE_ROOT


def _trim(value: str | None, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def serialize_thread_workspace(record: ThreadWorkspace) -> dict[str, Any]:
    from agent.modules.workspaces.host_mirror import mirror_summary
    workspace = _binding_from_record(record)
    return {
        "thread_id": record.thread_id,
        "workspace": workspace.model_dump() if workspace else None,
        "host_mirror": mirror_summary(record),
        "sync_policy": record.sync_policy,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _execution_from_record(record: ThreadWorkspace) -> WorkspaceRef | None:
    locator = record.execution_locator
    if not locator:
        return None
    return workspace_ref_from_columns(
        backend=record.execution_backend,
        locator=locator,
        label=record.execution_label,
        metadata_json=record.execution_metadata_json,
    )


def _binding_from_record(record: ThreadWorkspace) -> WorkspaceBinding | None:
    execution = _execution_from_record(record)
    if execution is None:
        return None
    return binding_from_record(record, execution)


async def _policy_for_workspace(session, ref):
    from agent.modules.projects.workspaces import source_key
    from agent.modules.workspaces.policies import MANUAL_APPLY, sync_policy
    peers = (await session.execute(select(ThreadWorkspace).where(
        ThreadWorkspace.execution_backend == ref.backend,
        ThreadWorkspace.execution_locator == ref.locator,
    ))).scalars().all()
    return next((sync_policy(peer) for peer in peers if _execution_from_record(peer) is not None
                 and source_key(_execution_from_record(peer)) == source_key(ref)), MANUAL_APPLY)


class ThreadWorkspaceRepository:
    async def upsert(
        self,
        *,
        thread_id: str,
        workspace: WorkspaceRef | dict[str, Any] | str,
    ) -> dict[str, Any]:
        now = utcnow()
        normalized_thread_id = _trim(resolve_thread_id(thread_id), 512)
        from agent.shared.config.service import get_config_service

        default_locator = str(
            get_config_service().get_path("workspace.root", DEFAULT_WORKSPACE_ROOT)
        )
        workspace_ref = normalize_workspace_ref(
            workspace,
            default_locator=default_locator,
        )
        binding = WorkspaceBinding.model_validate(workspace) if isinstance(workspace, (WorkspaceBinding, dict)) and (
            isinstance(workspace, WorkspaceBinding) or "scope" in workspace
        ) else bind_workspace_ref(workspace_ref)
        if not normalized_thread_id:
            raise ValueError("Thread ID is required.")
        if not workspace_ref.locator:
            raise ValueError("Workspace locator is required.")

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id == normalized_thread_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                record = ThreadWorkspace(
                    thread_id=normalized_thread_id,
                    sync_policy=await _policy_for_workspace(session, workspace_ref),
                    working_dir=workspace_ref.locator,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                previous_ref = _execution_from_record(record)
                from agent.modules.projects.workspaces import source_key
                if previous_ref is not None and source_key(previous_ref) != source_key(workspace_ref):
                    # The policy belongs to the workspace identity; a new execution ref must not rewrite a migrated row.
                    record.host_mirror_json = "{}"
                record.working_dir = workspace_ref.locator
                record.updated_at = now
                if record.project_id and not binding.project_id:
                    from agent.modules.projects import get_project_workspace_service, source_key

                    previous = _execution_from_record(record)
                    if previous is not None and source_key(previous) == source_key(workspace_ref):
                        binding = binding_from_record(record, workspace_ref)
                    else:
                        binding = await get_project_workspace_service().find_binding(workspace_ref) or binding
                        record.project_id, record.project_workspace_id = binding.project_id, binding.project_workspace_id
            if not record.host_mirror_json or record.host_mirror_json == "{}":
                record.host_mirror_json = json.dumps({"generation": token_hex(32)})
            record.scope_key = binding.scope.key
            record.scope_kind = binding.scope.kind
            record.scope_label = binding.scope.label
            record.scope_metadata_json = json.dumps(
                binding.scope.metadata,
                ensure_ascii=False,
                sort_keys=True,
            )
            record.execution_backend = binding.execution.backend
            record.execution_locator = binding.execution.locator
            record.execution_label = binding.execution.label
            record.execution_metadata_json = json.dumps(
                binding.execution.metadata,
                ensure_ascii=False,
                sort_keys=True,
            )
            record.workspace_backend = None
            record.workspace_locator = None
            record.workspace_label = None
            record.workspace_metadata_json = None

            from agent.modules.projects import register_thread_project

            owned = await register_thread_project(session, normalized_thread_id, binding)
            record.project_id, record.project_workspace_id = owned.project_id, owned.project_workspace_id
            record.scope_key, record.scope_kind, record.scope_label = owned.scope.key, owned.scope.kind, owned.scope.label
            record.scope_metadata_json = json.dumps(owned.scope.metadata)
            await session.commit()
            await session.refresh(record)
            return serialize_thread_workspace(record)

    async def get(self, thread_id: str) -> dict[str, Any] | None:
        normalized_thread_id = _trim(resolve_thread_id(thread_id), 512)
        if not normalized_thread_id:
            return None

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id == normalized_thread_id
                )
            )
            record = result.scalar_one_or_none()
            return serialize_thread_workspace(record) if record else None

    async def delete(self, thread_id: str) -> bool:
        normalized_thread_id = _trim(resolve_thread_id(thread_id), 512)
        if not normalized_thread_id:
            return False

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id == normalized_thread_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                return False

            await session.delete(record)
            await session.commit()
            return True

    async def list_by_thread_ids(
        self,
        thread_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        normalized_thread_ids = list(
            dict.fromkeys(
                thread_id
                for thread_id in (_trim(resolve_thread_id(thread_id), 512) for thread_id in thread_ids)
                if thread_id
            )
        )
        if not normalized_thread_ids:
            return {}

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id.in_(normalized_thread_ids)
                )
            )
            return {
                record.thread_id: serialize_thread_workspace(record)
                for record in result.scalars().all()
            }

    async def list_by_backend(self, backend: str) -> dict[str, dict[str, Any]]:
        normalized_backend = _trim(backend, 50)
        if not normalized_backend:
            return {}

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.execution_backend == normalized_backend
                )
            )
            return {
                record.thread_id: serialize_thread_workspace(record)
                for record in result.scalars().all()
            }

    async def has_other_conversations(self, workspace: WorkspaceRef, thread_id: str) -> bool:
        """Check whether another root conversation still uses this execution workspace."""
        root_thread = resolve_thread_id(thread_id).split(":sub:", 1)[0]
        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace.thread_id).where(
                    ThreadWorkspace.execution_backend == workspace.backend,
                    ThreadWorkspace.execution_locator == workspace.locator,
                )
            )
            return any(resolve_thread_id(value).split(":sub:", 1)[0] != root_thread
                       for value in result.scalars().all())

    async def update_metadata(
        self,
        *,
        thread_id: str,
        metadata: dict[str, Any],
        expected_backend: str | None = None,
    ) -> WorkspaceRef | None:
        normalized_thread_id = _trim(resolve_thread_id(thread_id), 512)
        if not normalized_thread_id:
            return None

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id == normalized_thread_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            if expected_backend and record.execution_backend != expected_backend:
                return None

            current_metadata: dict[str, Any] = {}
            if record.execution_metadata_json:
                try:
                    parsed = json.loads(record.execution_metadata_json)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    current_metadata = parsed
            current_metadata.update(metadata)
            record.execution_metadata_json = json.dumps(
                current_metadata,
                ensure_ascii=False,
                sort_keys=True,
            )
            execution = _execution_from_record(record)
            if execution is not None:
                binding = bind_workspace_ref(execution)
                record.scope_key = binding.scope.key
                record.scope_kind = binding.scope.kind
                record.scope_label = binding.scope.label
                record.scope_metadata_json = json.dumps(
                    binding.scope.metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            record.updated_at = utcnow()
            await session.commit()
            await session.refresh(record)
            return _execution_from_record(record)


_sync_engine: Engine | None = None
_sync_engine_lock = threading.Lock()


def _get_sync_engine() -> Engine:
    """Return a process-wide cached sync engine.

    The lifecycle sweeper calls ``update_thread_workspace_metadata_sync`` for
    every Daytona thread on each tick. Building a new ``Engine`` per call
    adds a non-trivial amount of overhead (connection pool, dialect wiring),
    so we cache one and reuse it. The engine is safe to share because
    SQLAlchemy's ``Engine`` is documented as thread-safe for connection
    acquisition.
    """
    global _sync_engine
    if _sync_engine is not None:
        return _sync_engine
    with _sync_engine_lock:
        if _sync_engine is None:
            _sync_engine = create_engine(
                _normalize_url_to_sync(get_database_url()),
                future=True,
            )
    return _sync_engine


def reset_sync_engine() -> None:
    """Drop the cached sync engine.

    Tests that mutate ``get_database_url`` between cases should call this to
    ensure the next ``update_thread_workspace_metadata_sync`` call binds to
    the new URL.
    """
    global _sync_engine
    with _sync_engine_lock:
        if _sync_engine is not None:
            _sync_engine.dispose()
            _sync_engine = None


def update_thread_workspace_metadata_sync(
    *,
    thread_id: str,
    metadata: dict[str, Any],
    expected_backend: str | None = None,
) -> WorkspaceRef | None:
    normalized_thread_id = _trim(resolve_thread_id(thread_id), 512)
    if not normalized_thread_id:
        return None

    from sqlalchemy.orm import Session

    engine = _get_sync_engine()
    with Session(engine) as session:
        result = session.execute(
            select(ThreadWorkspace).where(
                ThreadWorkspace.thread_id == normalized_thread_id
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        if expected_backend and record.execution_backend != expected_backend:
            return None

        current_metadata: dict[str, Any] = {}
        if record.execution_metadata_json:
            try:
                parsed = json.loads(record.execution_metadata_json)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                current_metadata = parsed
        current_metadata.update(metadata)
        record.execution_metadata_json = json.dumps(
            current_metadata,
            ensure_ascii=False,
            sort_keys=True,
        )
        execution = _execution_from_record(record)
        if execution is not None:
            binding = bind_workspace_ref(execution)
            record.scope_key = binding.scope.key
            record.scope_kind = binding.scope.kind
            record.scope_label = binding.scope.label
            record.scope_metadata_json = json.dumps(
                binding.scope.metadata,
                ensure_ascii=False,
                sort_keys=True,
            )
        record.updated_at = utcnow()
        session.commit()
        session.refresh(record)
        return _execution_from_record(record)


def get_thread_workspace_repository(container=None) -> ThreadWorkspaceRepository:
    """Return container-scoped workspace repository."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).workspace_repository


__all__ = [
    "ThreadWorkspaceRepository",
    "get_thread_workspace_repository",
    "reset_sync_engine",
    "serialize_thread_workspace",
    "update_thread_workspace_metadata_sync",
]
