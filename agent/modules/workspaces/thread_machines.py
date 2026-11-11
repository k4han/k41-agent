"""Ownership of auxiliary machines and snapshots that agents create for a conversation.

The main workspace of a conversation lives in ``thread_workspaces``. Anything an
agent creates on top of it (extra machines, their snapshots) is recorded here,
under the root conversation, so that tools can refuse resources owned by other
conversations and so that cleanup can find what each conversation left behind.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from agent.modules.workspaces.models import ThreadMachine
from agent.shared.infrastructure.db.base import utcnow

MACHINE = "machine"
SNAPSHOT = "snapshot"
CREATING = "creating"
ACTIVE = "active"
DELETED = "deleted"
RELEASED = "released"
LIVE_STATUSES = (CREATING, ACTIVE)
TOUCH_INTERVAL = timedelta(seconds=60)
_PENDING_PREFIX = "pending:"
_reservation_locks: dict[str, asyncio.Lock] = {}


class ThreadMachineLimitError(RuntimeError):
    """The conversation already owns the configured number of machines."""


@dataclass(frozen=True)
class ThreadMachineRecord:
    id: int
    thread_id: str
    kind: str
    backend: str
    locator: str
    root: str
    purpose: str
    status: str
    created_at: datetime | None
    last_used_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "thread_id": self.thread_id, "kind": self.kind, "backend": self.backend,
            "locator": self.locator, "root": self.root, "purpose": self.purpose, "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


def root_thread_id(thread_id: str | None) -> str:
    """Return the conversation that owns resources created by ``thread_id`` and its sub-agents."""
    from agent.shared.thread_ids import resolve_thread_id

    if not thread_id:
        return ""
    return resolve_thread_id(str(thread_id)).split(":sub:", 1)[0]


def _record(row: ThreadMachine) -> ThreadMachineRecord:
    return ThreadMachineRecord(
        id=row.id, thread_id=row.thread_id, kind=row.kind, backend=row.backend, locator=row.locator,
        root=row.root or "", purpose=row.purpose or "", status=row.status,
        created_at=row.created_at, last_used_at=row.last_used_at,
    )


async def _session():
    from agent.shared.infrastructure.db.session import get_async_session

    return await get_async_session()


async def reserve_thread_machine(
    thread_id: str, backend: str, *, limit: int, purpose: str = "", root: str = ""
) -> int:
    """Hold a slot before the provider call so parallel creates cannot exceed ``limit``."""
    owner = root_thread_id(thread_id)
    if not owner:
        raise ValueError("Auxiliary machines belong to a conversation; this run has none.")
    lock = _reservation_locks.setdefault(owner, asyncio.Lock())
    async with lock:
        session = await _session()
        async with session:
            rows = (await session.execute(select(ThreadMachine).where(
                ThreadMachine.thread_id == owner, ThreadMachine.kind == MACHINE,
                ThreadMachine.status.in_(LIVE_STATUSES)))).scalars().all()
            if limit > 0 and len(rows) >= limit:
                raise ThreadMachineLimitError(
                    f"This conversation already has {len(rows)} auxiliary machine(s); the limit is {limit}. "
                    "Delete one before creating another."
                )
            row = ThreadMachine(thread_id=owner, kind=MACHINE, backend=backend,
                                locator=_PENDING_PREFIX + uuid.uuid4().hex, root=root,
                                purpose=purpose[:500], status=CREATING)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return int(row.id)


async def activate_thread_machine(record_id: int, locator: str, *, root: str | None = None) -> None:
    session = await _session()
    async with session:
        row = await session.get(ThreadMachine, record_id)
        if row is None:
            raise LookupError("Auxiliary machine reservation is missing.")
        row.locator = locator
        row.status = ACTIVE
        if root is not None:
            row.root = root
        row.last_used_at = utcnow()
        await session.commit()


async def cancel_reservation(record_id: int) -> None:
    session = await _session()
    async with session:
        row = await session.get(ThreadMachine, record_id)
        if row is not None and row.status == CREATING:
            await session.delete(row)
            await session.commit()


async def record_thread_resource(
    thread_id: str, kind: str, backend: str, locator: str, *, root: str = "", purpose: str = ""
) -> None:
    """Record a resource that needs no reservation, such as a snapshot."""
    owner = root_thread_id(thread_id)
    if not owner:
        return
    session = await _session()
    async with session:
        row = await session.scalar(select(ThreadMachine).where(
            ThreadMachine.kind == kind, ThreadMachine.backend == backend, ThreadMachine.locator == locator))
        if row is None:
            session.add(ThreadMachine(thread_id=owner, kind=kind, backend=backend, locator=locator,
                                      root=root, purpose=purpose[:500], status=ACTIVE))
        elif row.thread_id == owner:
            row.status = ACTIVE
        await session.commit()


async def find_thread_resource(
    thread_id: str, locator: str, *, kind: str = MACHINE, backend: str | None = None
) -> ThreadMachineRecord | None:
    owner = root_thread_id(thread_id)
    if not owner or not locator:
        return None
    query = select(ThreadMachine).where(
        ThreadMachine.thread_id == owner, ThreadMachine.kind == kind,
        ThreadMachine.locator == locator, ThreadMachine.status == ACTIVE)
    if backend:
        query = query.where(ThreadMachine.backend == backend)
    session = await _session()
    async with session:
        row = await session.scalar(query)
        return _record(row) if row is not None else None


async def list_thread_resources(
    thread_id: str, *, kind: str | None = MACHINE, backend: str | None = None
) -> list[ThreadMachineRecord]:
    owner = root_thread_id(thread_id)
    if not owner:
        return []
    query = select(ThreadMachine).where(ThreadMachine.thread_id == owner, ThreadMachine.status == ACTIVE)
    if kind:
        query = query.where(ThreadMachine.kind == kind)
    if backend:
        query = query.where(ThreadMachine.backend == backend)
    session = await _session()
    async with session:
        return [_record(row) for row in (await session.execute(query.order_by(ThreadMachine.id))).scalars()]


async def touch_thread_resource(record: ThreadMachineRecord) -> None:
    """Refresh ``last_used_at`` at most once per interval to keep tool calls cheap."""
    now = utcnow()
    last = record.last_used_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        if now - last < TOUCH_INTERVAL:
            return
    session = await _session()
    async with session:
        await session.execute(update(ThreadMachine).where(ThreadMachine.id == record.id).values(last_used_at=now))
        await session.commit()


async def mark_resource_deleted(backend: str, locator: str, *, kind: str = MACHINE) -> None:
    session = await _session()
    async with session:
        await session.execute(update(ThreadMachine).where(
            ThreadMachine.kind == kind, ThreadMachine.backend == backend,
            ThreadMachine.locator == locator).values(status=DELETED))
        await session.commit()


async def release_thread_resources(thread_id: str) -> list[ThreadMachineRecord]:
    """Detach a deleted conversation's resources without destroying them.

    The machines keep running costs, so they are returned for the caller to
    report; the Sandboxes page lists them as unattached for manual cleanup.
    """
    owner = root_thread_id(thread_id)
    if not owner:
        return []
    session = await _session()
    async with session:
        rows = (await session.execute(select(ThreadMachine).where(
            ThreadMachine.thread_id == owner, ThreadMachine.status.in_(LIVE_STATUSES)))).scalars().all()
        released = [_record(row) for row in rows if row.status == ACTIVE]
        for row in rows:
            if row.status == CREATING:
                await session.delete(row)
            else:
                row.status = RELEASED
        await session.commit()
        return released


async def thread_machine_owners(backend: str) -> dict[str, str]:
    """Map live auxiliary machine locators of ``backend`` to their conversation."""
    session = await _session()
    async with session:
        rows = (await session.execute(select(ThreadMachine).where(
            ThreadMachine.kind == MACHINE, ThreadMachine.backend == backend,
            ThreadMachine.status == ACTIVE))).scalars().all()
        return {row.locator: row.thread_id for row in rows}


__all__ = [
    "ACTIVE",
    "MACHINE",
    "SNAPSHOT",
    "ThreadMachineLimitError",
    "ThreadMachineRecord",
    "activate_thread_machine",
    "cancel_reservation",
    "find_thread_resource",
    "list_thread_resources",
    "mark_resource_deleted",
    "record_thread_resource",
    "release_thread_resources",
    "reserve_thread_machine",
    "root_thread_id",
    "thread_machine_owners",
    "touch_thread_resource",
]
