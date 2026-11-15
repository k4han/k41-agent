"""Transactional queue and outbox shared by SQLite and PostgreSQL."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from agent.modules.agent_runtime.async_subagents.models import (
    SubagentExecutionRecord as Execution,
    SubagentNotificationRecord as Notification,
    SubagentTaskRecord as Task,
)
from agent.shared.async_subagents import LIVE_STATUSES, request_key
from agent.shared.infrastructure.db.session import get_async_session


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def task_dict(row: Task, *, private: bool = False) -> dict:
    value = {key: getattr(row, key) for key in (
        "task_id", "parent_thread_id", "root_thread_id", "thread_id", "tool_call_id",
        "agent_name", "request", "status", "version", "checkpoint_id", "result", "error",
        "created_at", "updated_at",
    )}
    value.update(run_id=row.active_job_id, interrupts=json.loads(row.interrupts_json),
                 execution_mode="async", delegation_id=row.task_id)
    if private:
        value.update(context=json.loads(row.context_json), parent_context=json.loads(row.parent_context_json),
                     parent_message_id=row.parent_message_id)
    return value


def job_dict(row: Execution) -> dict:
    value = {key: getattr(row, key) for key in (
        "job_id", "task_id", "thread_id", "root_thread_id", "kind", "status", "lease_owner",
        "lease_until", "attempt", "created_at", "error",
    )}
    value.update(context=json.loads(row.context_json), input=json.loads(row.input_json),
                 resume=json.loads(row.resume_json) if row.resume_json else None)
    return value


def notification_dict(row: Notification) -> dict:
    return {"event_id": row.event_id, "task_id": row.task_id, "version": row.version,
            "parent_thread_id": row.parent_thread_id, "payload": json.loads(row.payload_json)}


class AsyncSubagentRepository:
    def __init__(self, session_factory=None):
        self._session_factory = session_factory or get_async_session

    async def create_task(self, *, key: str, parent_thread_id: str, tool_call_id: str,
                          parent_message_id: str, agent_name: str, task: str,
                          context: dict, parent_context: dict) -> dict:
        now, task_id = time.time(), uuid.uuid4().hex
        root = parent_thread_id.split(":sub:", 1)[0]
        thread_id = f"{parent_thread_id}:sub:{task_id}"
        job_id = uuid.uuid4().hex
        try:
            async with await self._session_factory() as session, session.begin():
                existing = await session.scalar(select(Task).where(Task.request_key == key))
                if existing:
                    return task_dict(existing, private=True)
                row = Task(task_id=task_id, request_key=key, parent_thread_id=parent_thread_id,
                           root_thread_id=root, thread_id=thread_id, tool_call_id=tool_call_id,
                           parent_message_id=parent_message_id, agent_name=agent_name, request=task,
                           context_json=encode(context), parent_context_json=encode(parent_context),
                           status="pending", version=1, active_job_id=job_id,
                           created_at=now, updated_at=now)
                session.add(row)
                session.add(Execution(job_id=job_id, request_key=key, task_id=task_id,
                                      thread_id=thread_id, root_thread_id=root, kind="start",
                                      status="pending", context_json=encode(context),
                                      input_json=encode({"text": task}), created_at=now, updated_at=now))
                await session.flush()
                return task_dict(row, private=True)
        except IntegrityError:
            async with await self._session_factory() as session:
                existing = await session.scalar(select(Task).where(Task.request_key == key))
                if existing is None:
                    raise
                return task_dict(existing, private=True)

    async def get_task(self, parent_thread_id: str, task_id: str, *, private=False) -> dict | None:
        async with await self._session_factory() as session:
            row = await session.scalar(select(Task).where(
                Task.parent_thread_id == parent_thread_id, Task.task_id == task_id, Task.deleted_at.is_(None)))
            if row is None:
                return None
            value = task_dict(row, private=private)
            value["queued_updates"] = await session.scalar(select(func.count()).select_from(Execution).where(
                Execution.task_id == task_id, Execution.kind == "update", Execution.status == "pending"))
            return value

    async def get_task_for_execution(self, task_id: str) -> dict:
        async with await self._session_factory() as session:
            row = await session.scalar(select(Task).where(Task.task_id == task_id))
            if row is None:
                raise LookupError("Async task not found.")
            return task_dict(row, private=True)

    async def find_task_by_call(self, parent_thread_id: str, tool_call_id: str,
                                parent_message_id: str | None = None) -> dict | None:
        filters = [Task.parent_thread_id == parent_thread_id, Task.tool_call_id == tool_call_id,
                   Task.deleted_at.is_(None)]
        if parent_message_id:
            filters.append(Task.parent_message_id == parent_message_id)
        async with await self._session_factory() as session:
            row = await session.scalar(select(Task).where(*filters).order_by(Task.id.desc()).limit(1))
            return task_dict(row) if row else None

    async def list_tasks(self, parent_thread_id: str, *, status=None, limit=50, offset=0) -> dict:
        filters = [Task.parent_thread_id == parent_thread_id, Task.deleted_at.is_(None)]
        if status == "active":
            filters.append(Task.status.in_(LIVE_STATUSES))
        elif status:
            filters.append(Task.status == status)
        async with await self._session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(Task).where(*filters))
            active_scope = (Task.root_thread_id == parent_thread_id if ":sub:" not in parent_thread_id
                            else Task.parent_thread_id == parent_thread_id)
            active_count = await session.scalar(select(func.count()).select_from(Task).where(
                active_scope, Task.status.in_(LIVE_STATUSES), Task.deleted_at.is_(None)))
            rows = (await session.scalars(select(Task).where(*filters).order_by(Task.id.desc())
                                          .limit(limit).offset(offset))).all()
            tasks = [task_dict(row) for row in rows]
            for task in tasks:
                for key in ("result", "error", "interrupts"):
                    task.pop(key, None)
            return {"tasks": tasks, "total": count, "active_count": active_count, "limit": limit, "offset": offset}

    async def browser_handoffs(self, thread_id: str, owner_id: str) -> list[dict]:
        """Discover resumable browser requests, including sessions lost while waiting."""
        async with await self._session_factory() as session:
            rows = (await session.scalars(select(Task).where(
                or_(Task.parent_thread_id == thread_id, Task.thread_id == thread_id),
                Task.status == "waiting", Task.deleted_at.is_(None)).order_by(Task.id))).all()
            requests = []
            for row in rows:
                if json.loads(row.context_json).get("browser_owner_id") != owner_id:
                    continue
                for interrupt in json.loads(row.interrupts_json):
                    value = interrupt.get("value", {})
                    if (not isinstance(value, dict) or value.get("type") != "browser_handoff_request"
                            or value.get("thread_id") != row.thread_id):
                        continue
                    requests.append({**value, "task_id": row.task_id, "parent_thread_id": row.parent_thread_id,
                                     "agent_name": row.agent_name, "run_id": row.active_job_id,
                                     "interrupt_id": interrupt["id"]})
            return requests

    async def enqueue_update(self, parent_thread_id: str, task_id: str, instructions: str, key: str) -> dict:
        async with await self._session_factory() as session, session.begin():
            row = await session.scalar(select(Task).where(Task.task_id == task_id,
                Task.parent_thread_id == parent_thread_id, Task.deleted_at.is_(None)).with_for_update())
            if row is None:
                raise LookupError("Async task not found.")
            existing = await session.scalar(select(Execution).where(Execution.request_key == key))
            if existing is None:
                if row.status == "cancelled":
                    raise ValueError("A cancelled task cannot receive updates. Start a new task.")
                now = time.time()
                session.add(Execution(job_id=uuid.uuid4().hex, request_key=key, task_id=task_id,
                    thread_id=row.thread_id, root_thread_id=row.root_thread_id, kind="update", status="pending",
                    context_json=row.context_json, input_json=encode({"text": instructions}),
                    created_at=now, updated_at=now))
                row.version += 1
                row.updated_at = now
                if row.status not in LIVE_STATUSES:
                    row.status = "pending"
            await session.flush()
            return task_dict(row)

    async def resume_task(self, parent_thread_id: str, task_id: str, run_id: str,
                          interrupt_id: str, payload: dict, *, context: dict | None = None) -> dict:
        async with await self._session_factory() as session, session.begin():
            row = await session.scalar(select(Task).where(Task.task_id == task_id,
                Task.parent_thread_id == parent_thread_id, Task.deleted_at.is_(None)).with_for_update())
            if row is None:
                raise LookupError("Async task not found.")
            job = await session.scalar(select(Execution).where(Execution.job_id == row.active_job_id))
            resume = {"interrupt_id": interrupt_id, "payload": payload}
            if job and row.active_job_id == run_id and job.resume_json == encode(resume) and row.status in {"pending", "running"}:
                return task_dict(row)
            if (row.status != "waiting" or row.active_job_id != run_id or job is None
                    or interrupt_id not in {item["id"] for item in json.loads(row.interrupts_json)}):
                raise ValueError("The interrupt no longer belongs to the current waiting run.")
            pending = next(item for item in json.loads(row.interrupts_json) if item["id"] == interrupt_id)
            value = pending.get("value")
            browser_request = isinstance(value, dict) and value.get("type") == "browser_handoff_request"
            if browser_request:
                if payload.get("action") != "browser_handoff" or payload.get("handoff_id") != value.get("handoff_id"):
                    raise ValueError("The response does not match the waiting browser handoff.")
            elif payload.get("action") == "browser_handoff":
                raise ValueError("The interrupt is not a browser handoff.")
            job.resume_json, job.status, job.lease_until = encode(resume), "pending", 0
            row.status, row.updated_at = "pending", time.time()
            row.version += 1
            if context:
                row.context_json = job.context_json = encode(context)
                row.agent_name = context["agent_name"]
                await session.execute(update(Execution).where(Execution.task_id == task_id,
                    Execution.status == "pending").values(context_json=encode(context)))
            await session.execute(update(Notification).where(Notification.task_id == task_id,
                Notification.status == "pending").values(status="suppressed"))
            return task_dict(row)

    async def candidates(self, *, limit=100) -> list[dict]:
        async with await self._session_factory() as session:
            earlier = aliased(Execution)
            blocked = select(earlier.id).where(earlier.thread_id == Execution.thread_id,
                earlier.id < Execution.id, earlier.status.in_(LIVE_STATUSES)).exists()
            rows = (await session.scalars(select(Execution).where(or_(Execution.status == "pending",
                and_(Execution.status == "running", Execution.lease_until < time.time())), ~blocked)
                .order_by(Execution.id).limit(limit))).all()
            return [job_dict(row) for row in rows]

    async def claim(self, job_id: str, owner: str, *, lease_seconds=30) -> dict | None:
        async with await self._session_factory() as session, session.begin():
            now = time.time()
            candidate = await session.scalar(select(Execution).where(Execution.job_id == job_id))
            if candidate is None:
                return None
            task = None
            if candidate.task_id:
                task = await session.scalar(select(Task).where(Task.task_id == candidate.task_id).with_for_update())
                if task.deleted_at or task.status == "cancelled":
                    return None
            result = await session.execute(update(Execution).where(Execution.job_id == job_id,
                or_(Execution.status == "pending", and_(Execution.status == "running", Execution.lease_until < now)))
                .values(status="running", lease_owner=owner, lease_until=now + lease_seconds,
                        attempt=Execution.attempt + 1, updated_at=now))
            if result.rowcount != 1:
                return None
            row = await session.scalar(select(Execution).where(Execution.job_id == job_id))
            if task is not None:
                task.status, task.active_job_id, task.updated_at = "running", job_id, now
                task.version += 1
            if row.kind == "scheduled":
                from agent.modules.scheduler.models import SchedulerJobRun
                await session.execute(update(SchedulerJobRun).where(SchedulerJobRun.run_id == job_id)
                    .values(status="running"))
            return job_dict(row)

    async def heartbeat(self, job: dict, *, release=False) -> bool:
        async with await self._session_factory() as session, session.begin():
            result = await session.execute(update(Execution).where(Execution.job_id == job["job_id"],
                Execution.status == "running", Execution.lease_owner == job["lease_owner"],
                Execution.attempt == job["attempt"]).values(
                    **({"lease_until": 0} if release else {"lease_until": time.time() + 30})))
            return result.rowcount == 1

    async def refresh_context(self, job: dict, context: dict) -> None:
        async with await self._session_factory() as session, session.begin():
            await session.execute(update(Execution).where(Execution.job_id == job["job_id"],
                Execution.status == "running", Execution.lease_owner == job["lease_owner"],
                Execution.attempt == job["attempt"]).values(context_json=encode(context)))

    async def finish(self, job: dict, status: str, *, result="", error="", interrupts=(), checkpoint_id=None,
                     context=None) -> dict | None:
        async with await self._session_factory() as session, session.begin():
            task = (await session.scalar(select(Task).where(Task.task_id == job["task_id"]).with_for_update())
                    if job["task_id"] else None)
            row = await session.scalar(select(Execution).where(Execution.job_id == job["job_id"],
                Execution.status == "running", Execution.lease_owner == job["lease_owner"],
                Execution.attempt == job["attempt"]).with_for_update())
            if row is None:
                return None
            row.status, row.error, row.lease_until, row.updated_at = status, error, 0, time.time()
            if row.kind == "scheduled":
                from agent.modules.scheduler.executions import settle_run
                return await settle_run(session, row, status, result=result, error=error, context=context)
            if not row.task_id:
                return None
            if task.status == "cancelled" or task.deleted_at:
                return None
            task.status, task.result, task.error = status, result, error
            task.checkpoint_id, task.interrupts_json = checkpoint_id, encode(list(interrupts))
            task.version += 1
            task.updated_at = time.time()
            if context:
                task.context_json = encode(context)
                task.agent_name = context["agent_name"]
            await session.flush()
            queued = await session.scalar(select(Execution.id).where(Execution.task_id == task.task_id,
                Execution.status == "pending").limit(1))
            if queued and status != "waiting":
                task.status = "pending"
            payload = {**task_dict(task), "event_status": status}
            event_id = f"{row.job_id}:{task.version}"
            session.add(Notification(event_id=event_id, task_id=task.task_id, job_id=row.job_id,
                parent_thread_id=task.parent_thread_id, root_thread_id=task.root_thread_id,
                version=task.version, payload_json=encode(payload), context_json=task.parent_context_json,
                status="pending", created_at=time.time()))
            return payload

    async def pending_notifications(self, parent_thread_id: str) -> list[dict]:
        async with await self._session_factory() as session:
            rows = (await session.scalars(select(Notification).where(
                Notification.parent_thread_id == parent_thread_id, Notification.status == "pending")
                .order_by(Notification.id).limit(100))).all()
            return [notification_dict(row) for row in rows]

    async def acknowledge(self, parent_thread_id: str, cursors: dict) -> None:
        if not cursors:
            return
        async with await self._session_factory() as session, session.begin():
            jobs = (await session.scalars(select(Execution).where(Execution.thread_id == parent_thread_id,
                Execution.kind == "wakeup", Execution.status.in_(["pending", "waiting"]))
                .order_by(Execution.id).with_for_update())).all()
            for task_id, version in cursors.items():
                await session.execute(update(Notification).where(Notification.parent_thread_id == parent_thread_id,
                    Notification.task_id == task_id, Notification.version <= version, Notification.status == "pending")
                    .values(status="delivered"))
            for job in jobs:
                remaining = await session.scalar(select(Notification.id).where(
                    Notification.wake_job_id == job.job_id, Notification.status == "pending").limit(1))
                if remaining is None:
                    job.status = "completed"

    async def prepare_wakeups(self) -> None:
        async with await self._session_factory() as session, session.begin():
            running_wakeup = select(Execution.id).where(Execution.thread_id == Notification.parent_thread_id,
                Execution.kind == "wakeup", Execution.status == "running").exists()
            query = select(Notification).where(Notification.status == "pending",
                Notification.wake_job_id == "", ~running_wakeup).order_by(Notification.id).limit(100)
            parents: dict[str, list] = {}
            last_id, eligible = 0, 0
            while eligible < 100:
                rows = (await session.scalars(query.where(Notification.id > last_id))).all()
                if not rows:
                    break
                for row in rows:
                    last_id = row.id
                    payload = json.loads(row.payload_json)
                    if payload["event_status"] not in {"waiting", "cancelled"}:
                        parents.setdefault(row.parent_thread_id, []).append(row)
                        eligible += 1
                        if eligible == 100:
                            break
            for parent, events in parents.items():
                existing = await session.scalar(select(Execution).where(Execution.thread_id == parent,
                    Execution.kind == "wakeup", Execution.status.in_(["pending", "running"])))
                if existing and existing.status == "running":
                    continue
                if existing is None:
                    job_id, now = uuid.uuid4().hex, time.time()
                    existing = Execution(job_id=job_id, request_key=request_key("wake", parent, events[0].event_id),
                        task_id="", thread_id=parent, root_thread_id=events[0].root_thread_id, kind="wakeup",
                        status="pending", context_json=events[-1].context_json,
                        input_json=encode({}), created_at=now, updated_at=now)
                    session.add(existing)
                for event in events:
                    event.wake_job_id = existing.job_id

    async def cancel_scope(self, thread_id: str, *, delete=False) -> list[dict]:
        async with await self._session_factory() as session, session.begin():
            scope = or_(Task.thread_id == thread_id, Task.parent_thread_id == thread_id,
                        Task.thread_id.startswith(thread_id + ":sub:", autoescape=True))
            tasks = (await session.scalars(select(Task).where(scope, Task.deleted_at.is_(None))
                .order_by(Task.id).with_for_update())).all()
            ids = [task.task_id for task in tasks]
            now = time.time()
            for task in tasks:
                if task.status in LIVE_STATUSES:
                    task.status, task.error, task.interrupts_json = "cancelled", "Task was cancelled.", "[]"
                    task.version += 1
                    task.updated_at = now
                if delete:
                    task.deleted_at = now
            job_scope = or_(Execution.task_id.in_(ids), Execution.thread_id == thread_id,
                            Execution.thread_id.startswith(thread_id + ":sub:", autoescape=True))
            scheduled = list((await session.scalars(select(Execution.job_id).where(job_scope,
                Execution.kind == "scheduled", Execution.status.in_(LIVE_STATUSES)))).all())
            if scheduled:
                from agent.modules.scheduler.models import SchedulerJobRun
                from agent.shared.infrastructure.db import utcnow
                await session.execute(update(SchedulerJobRun).where(SchedulerJobRun.run_id.in_(scheduled))
                    .values(status="cancelled", error="Task was cancelled.", finished_at=utcnow()))
            await session.execute(update(Execution).where(job_scope, Execution.status.in_(LIVE_STATUSES))
                .values(status="cancelled", lease_until=0))
            await session.execute(update(Notification).where(or_(Notification.task_id.in_(ids),
                Notification.parent_thread_id == thread_id), Notification.status == "pending")
                .values(status="suppressed"))
            return [task_dict(task) for task in tasks]

    async def has_work(self, thread_id: str) -> bool:
        async with await self._session_factory() as session:
            live = await session.scalar(select(Task.id).where(or_(Task.parent_thread_id == thread_id,
                Task.thread_id.startswith(thread_id + ":sub:", autoescape=True)),
                Task.status.in_(LIVE_STATUSES), Task.deleted_at.is_(None)).limit(1))
            pending = await session.scalar(select(Notification.id).where(Notification.parent_thread_id == thread_id,
                Notification.status == "pending").limit(1))
            scheduled = await session.scalar(select(Execution.id).where(Execution.thread_id == thread_id,
                Execution.kind == "scheduled", Execution.status.in_(LIVE_STATUSES)).limit(1))
            return live is not None or pending is not None or scheduled is not None

    async def child_thread_ids(self, thread_id: str) -> list[str]:
        async with await self._session_factory() as session:
            return list((await session.scalars(select(Task.thread_id).where(or_(Task.parent_thread_id == thread_id,
                Task.thread_id.startswith(thread_id + ":sub:", autoescape=True))))).all())
