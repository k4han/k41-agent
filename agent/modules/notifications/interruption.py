"""Durable notification delivery and arbitration of human responses."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from urllib.parse import urlencode

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agent.modules.agent_runtime import (
    SubagentExecutionRecord as Execution,
    SubagentNotificationRecord,
    SubagentTaskRecord as Task,
)
from agent.modules.notifications.models import InterruptionRecord, NotificationDelivery
from agent.modules.notifications.settings import CHANNELS, NotificationSettings, load_settings, messages
from agent.modules.users import User, UserIdentity
from agent.shared.async_subagents import LIVE_STATUSES, request_key
from agent.shared.infrastructure.db import get_async_session, utcnow
from agent.shared.thread_ids import resolve_thread_id

logger = logging.getLogger(__name__)

ASK = "user_input_request"
BROWSER = "browser_handoff_request"
WAIT_TYPES = {ASK: "ask_user", BROWSER: "browser_handoff"}


class ResponseConflict(ValueError):
    """The exact request is no longer accepting responses."""


class RunAdmissionConflict(ResponseConflict):
    """A queued response owns the conversation's next run."""


def active_manager():
    from agent.bootstrap.container import get_active_container

    container = get_active_container()
    return getattr(container, "_interruption_timeout_manager", None)


def pending_interrupts(snapshot):
    return [{"id": interrupt.id, "value": interrupt.value}
            for task in snapshot.tasks for interrupt in task.interrupts]


def matches(value: dict, payload: dict) -> bool:
    return (value.get("type") == ASK and payload.get("action") == "answer") or (
        value.get("type") == BROWSER and payload.get("action") == "browser_handoff"
        and value.get("handoff_id") == payload.get("handoff_id"))


def identities_query():
    return (select(UserIdentity).join(User, UserIdentity.user_id == User.id)
            .where(User.is_active.is_(True), UserIdentity.platform.in_(CHANNELS)))


async def _insert_once(session, model, columns, **values):
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else postgres_insert
    await session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=columns))


class InterruptionTimeoutManager:
    def __init__(self, container, *, clock=time.time, session_factory=get_async_session):
        self.container = container
        self.clock = clock
        self.session_factory = session_factory
        self.presence: dict[tuple[str, str, str], float] = {}
        self.timer_signal = asyncio.Event()
        self.delivery_signal = asyncio.Event()
        self.tasks: list[asyncio.Task] = []

    @property
    def settings(self):
        try:
            return load_settings(self.container.config_service)
        except ValueError:
            logger.warning("Invalid notification settings; using defaults.")
            return NotificationSettings()

    def start(self):
        if not self.tasks:
            self.tasks = [
                asyncio.create_task(self._loop(self.tick, self.timer_signal), name="interruption-timeouts"),
                asyncio.create_task(self._loop(self.deliver, self.delivery_signal), name="notification-deliveries"),
                asyncio.create_task(self.recover(), name="notification-recovery"),
            ]

    async def shutdown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.presence.clear()

    async def _loop(self, operation, signal):
        while True:
            signal.clear()
            try:
                await operation()
            except Exception:
                logger.exception("Notification processing will retry.")
            try:
                await asyncio.wait_for(signal.wait(), timeout=1)
            except TimeoutError:
                pass

    def mark_presence(self, owner: str, thread_id: str, tab_id: str, visible: bool):
        now = self.clock()
        self.presence = {key: expiry for key, expiry in self.presence.items() if expiry > now}
        key = (owner, tab_id, thread_id)
        if visible:
            self.presence[key] = now + 35
        else:
            self.presence.pop(key, None)

    def _visible(self, owner, thread_id, now):
        return any(key[2] == thread_id and (not owner or key[0] == owner) and expiry > now
                   for key, expiry in self.presence.items())

    async def register(self, session, *, thread_id, root_thread_id, checkpoint_id, interrupts, context, job_id=""):
        if not checkpoint_id:
            return
        now = self.clock()
        settings = self.settings
        delay = (settings.dashboard_notify_delay_seconds
                 if self._visible(context.get("browser_owner_id"), root_thread_id, now) else 0)
        for interrupt in interrupts:
            value = interrupt["value"]
            if not isinstance(value, dict) or value.get("type") not in WAIT_TYPES or not interrupt["id"]:
                continue
            key = request_key("notification", thread_id, checkpoint_id, interrupt["id"])
            deadline = now + settings.timeout_minutes * 60
            if value["type"] == BROWSER and value.get("expires_at"):
                deadline = float(value["expires_at"])
            await _insert_once(
                session, InterruptionRecord, ["key"], key=key, thread_id=thread_id,
                root_thread_id=root_thread_id, job_id=job_id, checkpoint_id=checkpoint_id,
                interrupt_id=interrupt["id"], context_json=json.dumps(context),
                request_json=json.dumps(value), fallback_prompt=settings.fallback_prompt,
                timeout_minutes=int(value.get("timeout_minutes") or settings.timeout_minutes), deadline=deadline,
                notify_at=now + delay, status="waiting",
            )
        self.timer_signal.set()

    async def observe(self, graph, thread_id, context):
        try:
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            interrupts = pending_interrupts(snapshot)
            if interrupts:
                async with await self.session_factory() as session, session.begin():
                    await self.register(session, thread_id=thread_id, root_thread_id=thread_id,
                                        checkpoint_id=snapshot.config["configurable"].get("checkpoint_id"),
                                        interrupts=interrupts, context=context)
        except Exception:
            logger.exception("Could not register a paused conversation.")

    async def run_context(self, context, workflow, usage_context):
        from agent.modules.agent_runtime import _run_context

        result = {**_run_context(context), "workflow": workflow, "usage_context": usage_context}
        if usage_context.get("platform") in CHANNELS:
            async with await self.session_factory() as session:
                identity = await session.scalar(identities_query().where(
                    UserIdentity.platform == usage_context["platform"],
                    UserIdentity.external_id == str(usage_context["user_id"]),
                ))
            result["_notification_user_id"] = identity.user_id if identity else -1
        return result

    async def has_paused_run(self, thread_id):
        async with await self.session_factory() as session:
            waiting = await session.scalar(select(InterruptionRecord.id).where(
                InterruptionRecord.thread_id == thread_id, InterruptionRecord.status == "waiting",
            ).limit(1))
            if waiting is not None:
                return True
            return bool(await session.scalar(select(Execution.id).where(
                Execution.thread_id == thread_id, Execution.kind == "notification_resume",
                Execution.status.in_(["pending", "running", "waiting"]),
            ).limit(1)))

    async def paused_run_outcome(self, thread_id):
        async with await self.session_factory() as session:
            row = await session.scalar(select(Execution).where(
                Execution.thread_id == thread_id, Execution.kind == "notification_resume",
            ).order_by(Execution.id.desc()).limit(1))
        if row is None or row.status in LIVE_STATUSES:
            return None
        result = ""
        if row.status == "completed":
            from agent.modules.agent_runtime import AsyncSubagentService

            try:
                checkpoint_id = json.loads(row.context_json).get("_notification_checkpoint_id")
                snapshot = await self.snapshot(row, checkpoint_id=checkpoint_id)
                if (snapshot.metadata or {}).get("k41_execution_id") == row.job_id:
                    result = AsyncSubagentService._saved_result(snapshot)
            except Exception:
                logger.warning("Could not restore the result of execution %s.", row.job_id, exc_info=True)
        return {"status": row.status, "result": result, "error": row.error, "completed_at": row.updated_at}

    async def snapshot(self, row, *, checkpoint_id=None):
        from agent.modules.workflows import get_workflow_graph

        graph = get_workflow_graph(json.loads(row.context_json)["workflow"])
        config = {"thread_id": row.thread_id}
        if checkpoint_id:
            config["checkpoint_id"] = checkpoint_id
        return await graph.aget_state({"configurable": config})

    async def is_current(self, row):
        if row.job_id:
            async with await self.session_factory() as session:
                status = await session.scalar(select(Execution.status).where(Execution.job_id == row.job_id))
            if status != "waiting":
                return False
        snapshot = await self.snapshot(row)
        return (snapshot.config["configurable"].get("checkpoint_id") == row.checkpoint_id
                and any(item["id"] == row.interrupt_id for item in pending_interrupts(snapshot)))

    async def invalidate(self, thread_id, *, tree=True):
        thread_id = resolve_thread_id(thread_id)
        scope = InterruptionRecord.thread_id == thread_id
        if tree:
            scope = or_(scope, InterruptionRecord.root_thread_id == thread_id)
        async with await self.session_factory() as session, session.begin():
            result = await session.execute(update(InterruptionRecord).where(
                scope, InterruptionRecord.status == "waiting").values(status="cancelled"))
        return bool(result.rowcount)

    async def cancel_thread(self, thread_id: str) -> bool:
        thread_id = resolve_thread_id(thread_id)
        invalidated = await self.invalidate(thread_id)
        stopped = await self.container.async_subagents.cancel_tree(thread_id)
        settled = await self.container.task_manager.settle_paused(
            thread_id, "cancelled", error="Task was cancelled.",
        )
        return invalidated or stopped or settled

    async def browser_status(self, thread_id, handoff_id):
        async with await self.session_factory() as session:
            rows = (await session.scalars(select(InterruptionRecord).where(
                InterruptionRecord.thread_id == thread_id,
                InterruptionRecord.status.in_(["waiting", "answered", "timed_out"]),
            ))).all()
            return next((row.status for row in rows
                         if json.loads(row.request_json).get("handoff_id") == handoff_id), None)

    async def prepare_new_run(self, thread_id, *, check_only=False):
        async with await self.session_factory() as session, session.begin():
            if not check_only:
                resumable = select(Execution.job_id).where(
                    Execution.thread_id == thread_id, Execution.kind.in_(["notification_resume", "wakeup"]),
                    Execution.status == "waiting",
                )
                await session.execute(update(InterruptionRecord).where(
                    InterruptionRecord.thread_id == thread_id, InterruptionRecord.status == "waiting",
                    or_(InterruptionRecord.job_id == "", InterruptionRecord.job_id.in_(resumable)),
                ).values(status="cancelled"))
                await session.execute(update(Execution).where(Execution.job_id.in_(resumable)).values(status="cancelled"))
            queued = await session.scalar(select(Execution.id).where(
                Execution.thread_id == thread_id, Execution.kind == "notification_resume",
                Execution.status.in_(["pending", "running"])).limit(1))
            if queued is not None:
                raise RunAdmissionConflict("A response is already queued for this conversation.")

    async def accept_manual(self, *, thread_id, interrupt_id, payload, owner_id, job_id=None, task_id=None) -> str | None:
        if payload.get("action") not in {"answer", "browser_handoff"}:
            if job_id or task_id:
                return None
            return await self._accept_managed_response(thread_id, interrupt_id, payload, owner_id)
        async with await self.session_factory() as session:
            query = select(InterruptionRecord)
            if job_id:
                query = query.where(InterruptionRecord.job_id == job_id)
            else:
                query = query.where(InterruptionRecord.thread_id == thread_id)
            rows = (await session.scalars(query.order_by(InterruptionRecord.id.desc()))).all()
        tracked = bool(rows)
        if interrupt_id:
            rows = [row for row in rows if row.interrupt_id == interrupt_id]
        matches_request = [row for row in rows if matches(json.loads(row.request_json), payload)]
        if not matches_request:
            if tracked:
                raise ResponseConflict("The response does not match the pending request.")
            return None
        waiting = [row for row in matches_request if row.status == "waiting"]
        try:
            if len(waiting) != 1:
                raise ResponseConflict("This request was already handled or requires an exact interrupt ID.")
            row = waiting[0]
            self._check_manual_scope(row, thread_id, owner_id, task_id)
            await self.accept(row, payload)
            return row.job_id or row.key
        except ResponseConflict:
            if not (job_id and interrupt_id):
                raise
            async with await self.session_factory() as session:
                saved = await session.scalar(select(Execution.resume_json).where(
                    Execution.job_id == job_id, Execution.status.in_(["pending", "running"]),
                ))
                resume = json.loads(saved) if saved else {}
                if resume.get("interrupt_id") != interrupt_id or resume.get("payload") != payload:
                    raise
                row = await session.scalar(select(InterruptionRecord).where(
                    InterruptionRecord.key == resume.get("notification_key"), InterruptionRecord.job_id == job_id,
                    InterruptionRecord.status == "answered",
                ))
            if row is None:
                raise
            self._check_manual_scope(row, thread_id, owner_id, task_id)
            return job_id

    @staticmethod
    def _check_manual_scope(row, thread_id, owner_id, task_id):
        context = json.loads(row.context_json)
        if (context.get("browser_owner_id") and context["browser_owner_id"] != owner_id
                or task_id and context.get("_notification_task_id") != task_id
                or task_id and context.get("_notification_parent") != thread_id):
            raise ResponseConflict("This request belongs to another conversation or account.")

    async def _accept_managed_response(self, thread_id, interrupt_id, payload, owner_id) -> str | None:
        from agent.modules.acp import PERMISSION_INTERRUPT_TYPE
        from agent.modules.tools import PLAN_REVIEW_INTERRUPT_TYPE

        action = payload.get("action")
        request_type = {
            "permission": "permission_request", "acp_permission": PERMISSION_INTERRUPT_TYPE,
            "approve": PLAN_REVIEW_INTERRUPT_TYPE, "revise": PLAN_REVIEW_INTERRUPT_TYPE,
        }.get(action)
        if request_type is None:
            return None
        async with await self.session_factory() as session:
            jobs = (await session.scalars(select(Execution).where(
                Execution.thread_id == thread_id, Execution.status.in_(LIVE_STATUSES),
                or_(Execution.kind == "notification_resume", and_(
                    Execution.kind == "wakeup", or_(Execution.status != "pending", Execution.resume_json.is_not(None)),
                )),
            ).order_by(Execution.id))).all()
        for job in jobs:
            snapshot = await self.snapshot(job)
            checkpoint_id = snapshot.config["configurable"].get("checkpoint_id")
            if (snapshot.metadata or {}).get("k41_execution_id") == job.job_id:
                break
            if checkpoint_id and job.resume_json and json.loads(job.resume_json).get("checkpoint_id") == checkpoint_id:
                raise ResponseConflict("A response is already queued for this conversation.")
        else:
            return None
        if job.status != "waiting":
            raise ResponseConflict("A response is already queued for this conversation.")
        context = json.loads(job.context_json)
        if context.get("browser_owner_id") and context["browser_owner_id"] != owner_id:
            raise ResponseConflict("This request belongs to another conversation or account.")
        requests = [item for item in pending_interrupts(snapshot) if isinstance(item["value"], dict)
                    and item["value"].get("type") == request_type and (not interrupt_id or item["id"] == interrupt_id)
                    and item["value"].get("request_id") == payload.get("request_id")]
        if len(requests) != 1 or not checkpoint_id:
            raise ResponseConflict("The response does not match the pending request.")
        if action == "approve":
            from agent.modules.agent_runtime import (
                _get_plan_resume_source_agent_name, _normalize_human_resume_payload, _resolve_agent_name_for_resume,
            )

            normalized = _normalize_human_resume_payload(payload)
            source = await _get_plan_resume_source_agent_name(thread_id, context["agent_name"], normalized)
            _resolve_agent_name_for_resume(self.container.agent_catalog_service, context["agent_name"],
                                           normalized, source_agent_name=source)
        resume = {"interrupt_id": requests[0]["id"], "payload": payload, "checkpoint_id": checkpoint_id}
        async with await self.session_factory() as session, session.begin():
            result = await session.execute(update(Execution).where(
                Execution.job_id == job.job_id, Execution.status == "waiting", Execution.attempt == job.attempt,
            ).values(status="pending", resume_json=json.dumps(resume), lease_until=0, updated_at=self.clock()))
            if result.rowcount != 1:
                raise ResponseConflict("This request was already handled.")
        await self.container.async_subagents.start()
        self.container.async_subagents._signal.set()
        return job.job_id

    async def accept(self, row, payload, *, timeout=False, identity_id=None, external_id=None, paired_user_id=None):
        from agent.modules.scheduler import SchedulerJobRun
        from agent.modules.tools import (
            AskUserAnswerResumePayload, AskUserInput, _validate_answer_payload,
        )

        value = json.loads(row.request_json)
        if not timeout:
            if not matches(value, payload):
                raise ResponseConflict("The response does not match the pending request.")
            if value["type"] == ASK:
                questions = AskUserInput.model_validate(value)
                _validate_answer_payload(questions, AskUserAnswerResumePayload.model_validate(payload))
        if not await self.is_current(row):
            await self._close_stale(row)
            raise ResponseConflict("The checkpoint no longer has this pending request.")

        context = json.loads(row.context_json)
        new_status = "timed_out" if timeout else "answered"
        if timeout:
            minutes = str(row.timeout_minutes)
            if value["type"] == ASK:
                payload = {"action": "ask_user_timeout",
                           "fallback_prompt": row.fallback_prompt.replace("{timeout_minutes}", minutes)}
            else:
                payload = {"action": "browser_handoff", "handoff_id": value["handoff_id"], "decision": "return"}
        resume = {"interrupt_id": row.interrupt_id, "payload": payload,
                  "checkpoint_id": row.checkpoint_id, "notification_key": row.key}
        async with await self.session_factory() as session, session.begin():
            if identity_id is not None:
                destination = await session.scalar(identities_query().where(
                    UserIdentity.id == identity_id, UserIdentity.platform == "telegram",
                    UserIdentity.external_id == external_id,
                ).with_for_update())
                if (destination is None or identity_id not in self.settings.recipient_identity_ids
                        or paired_user_id is not None and destination.user_id != paired_user_id
                        or context.get("_notification_user_id") not in (None, destination.user_id)):
                    raise PermissionError("This Telegram identity cannot answer the request.")
            job = None
            task_id = context.get("_notification_task_id")
            if task_id:
                task = await session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
                if not task or task.status != "waiting" or task.active_job_id != row.job_id or task.deleted_at:
                    raise ResponseConflict("The background task is no longer waiting.")
            now = self.clock()
            deadline = InterruptionRecord.deadline <= now if timeout else InterruptionRecord.deadline > now
            result = await session.execute(update(InterruptionRecord).where(
                InterruptionRecord.key == row.key, InterruptionRecord.status == "waiting", deadline,
            ).values(status=new_status))
            if result.rowcount != 1:
                raise ResponseConflict("This request was already handled or has expired.")
            if row.job_id:
                job = await session.scalar(select(Execution).where(Execution.job_id == row.job_id).with_for_update())
                if not job or job.status != "waiting":
                    raise ResponseConflict("The execution is no longer waiting.")
                job.status, job.resume_json, job.lease_until = "pending", json.dumps(resume, separators=(",", ":")), 0
                if task_id:
                    task.status, task.updated_at = "pending", now
                    task.version += 1
                    await session.execute(update(SubagentNotificationRecord).where(
                        SubagentNotificationRecord.task_id == task_id,
                        SubagentNotificationRecord.status == "pending",
                    ).values(status="suppressed"))
                if job.kind == "scheduled":
                    await session.execute(update(SchedulerJobRun).where(SchedulerJobRun.run_id == job.job_id)
                                          .values(status="pending", finished_at=None))
            else:
                session.add(Execution(job_id=row.key, request_key=row.key, task_id="", thread_id=row.thread_id,
                                      root_thread_id=row.root_thread_id, kind="notification_resume",
                                      status="pending", context_json=row.context_json,
                                      input_json="{}", resume_json=json.dumps(resume), created_at=now, updated_at=now))
            if timeout:
                await self.enqueue(session, f"{row.key}:timeout", "interruption_timeout", row.root_thread_id,
                                   messages()["timeout"].replace("{timeout_minutes}", str(row.timeout_minutes)),
                                   interruption_key=row.key, user_id=context.get("_notification_user_id"))
        await self.container.async_subagents.start()
        self.container.async_subagents._signal.set()
        self.delivery_signal.set()

    async def _close_stale(self, row):
        async with await self.session_factory() as session, session.begin():
            await session.execute(update(InterruptionRecord).where(
                InterruptionRecord.key == row.key, InterruptionRecord.status == "waiting").values(status="cancelled"))

    async def tick(self):
        now = self.clock()
        async with await self.session_factory() as session:
            rows = (await session.scalars(select(InterruptionRecord).where(
                InterruptionRecord.status == "waiting",
                or_(InterruptionRecord.deadline <= now, InterruptionRecord.notify_at <= now),
            ).order_by(InterruptionRecord.deadline).limit(100))).all()
        for row in rows:
            try:
                if row.deadline <= self.clock():
                    await self.accept(row, {}, timeout=True)
                elif await self.is_current(row):
                    value = json.loads(row.request_json)
                    event = WAIT_TYPES[value["type"]]
                    details = value.get("reason") if value["type"] == BROWSER else value.get("title")
                    body = "\n\n".join(filter(None, (messages()[event], details)))
                    async with await self.session_factory() as session, session.begin():
                        await self.enqueue(session, row.key, event, row.root_thread_id, body,
                                           interruption_key=row.key,
                                           user_id=json.loads(row.context_json).get("_notification_user_id"))
                        await session.execute(update(InterruptionRecord).where(
                            InterruptionRecord.key == row.key, InterruptionRecord.status == "waiting",
                        ).values(notify_at=row.deadline))
                else:
                    await self._close_stale(row)
            except ResponseConflict:
                continue
            except Exception:
                logger.exception("Could not process interruption %s", row.key)
        self.delivery_signal.set()

    async def enqueue(self, session, event_key, event, thread_id, text, *, interruption_key=None,
                      user_id=None):
        settings = self.settings
        if event not in settings.events or not settings.recipient_identity_ids:
            return set()
        query = identities_query().where(UserIdentity.id.in_(settings.recipient_identity_ids))
        if user_id is not None:
            query = query.where(UserIdentity.user_id == user_id)
        recipients = (await session.scalars(query)).all()
        for identity in recipients:
            await _insert_once(
                session, NotificationDelivery, ["event_key", "identity_id"],
                event_key=event_key, event_type=event, identity_id=identity.id, user_id=identity.user_id,
                interruption_key=interruption_key, thread_id=thread_id, text=text,
                due_at=self.clock(), status="pending",
                token=secrets.token_urlsafe(16) if event == "ask_user" and identity.platform == "telegram" else None,
            )
        return {(identity.platform, identity.external_id) for identity in recipients}

    async def publish_error(self, key, source, thread_id, *, error=None, rate=False):
        from agent.modules.providers import classify_pool_failure
        from agent.shared.infrastructure.errors import classify_agent_error

        failure = classify_pool_failure(error) if error is not None else None
        rate = rate or (failure is not None and failure.code in {"rate_limit", "quota"})
        event = "rate_limit" if rate else "background_failure"
        code = failure.code if failure else classify_agent_error(error).code if error is not None else "interrupted"
        detail = "Provider quota or rate limit reached." if rate else "Background execution failed or was interrupted."
        async with await self.session_factory() as session, session.begin():
            recipients = await self.enqueue(session, request_key(event, key), event, thread_id,
                                            f"{source}\n{detail} ({code})")
        self.delivery_signal.set()
        return recipients

    def link(self, delivery, row=None):
        return self.dashboard_link(delivery.thread_id, row)

    def dashboard_link(self, thread_id, row=None):
        origin = self.settings.dashboard_base_url
        if not origin:
            return ""
        if not thread_id:
            return f"{origin}/settings/logs"
        query = {"thread_id": thread_id}
        if row is not None:
            context = json.loads(row.context_json)
            if context.get("_notification_task_id"):
                query["task_id"] = context["_notification_task_id"]
            if json.loads(row.request_json).get("type") == BROWSER:
                query["browser_thread_id"] = row.thread_id
        return f"{origin}/chat?{urlencode(query)}"

    async def deliver(self):
        now = self.clock()
        async with await self.session_factory() as session:
            ids = (await session.scalars(select(NotificationDelivery.id).where(
                NotificationDelivery.status.in_(["pending", "sending"]), NotificationDelivery.due_at <= now,
            ).order_by(NotificationDelivery.due_at, NotificationDelivery.id).limit(20))).all()
        results = await asyncio.gather(*(self._deliver_one(delivery_id) for delivery_id in ids), return_exceptions=True)
        for delivery_id, result in zip(ids, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Could not process notification delivery %s.", delivery_id, exc_info=result)

    async def _deliver_one(self, delivery_id):
        from agent.modules.notifications.service import send_notification
        from agent.modules.notifications.telegram import send_request

        async with await self.session_factory() as session, session.begin():
            now = self.clock()
            result = await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.id == delivery_id, NotificationDelivery.status.in_(["pending", "sending"]),
                NotificationDelivery.due_at <= now,
            ).values(status="sending", due_at=now + 120, attempts=NotificationDelivery.attempts + 1))
            if result.rowcount != 1:
                return
            delivery = await session.get(NotificationDelivery, delivery_id)
            identity = await session.scalar(identities_query().where(
                UserIdentity.id == delivery.identity_id, UserIdentity.user_id == delivery.user_id))
            row = await session.scalar(select(InterruptionRecord).where(
                InterruptionRecord.key == delivery.interruption_key)) if delivery.interruption_key else None
            settings = self.settings
            if (identity is None or identity.id not in settings.recipient_identity_ids
                    or delivery.event_type not in settings.events or not settings.dashboard_base_url
                    or delivery.event_type in WAIT_TYPES.values()
                    and (row is None or row.status != "waiting" or row.deadline <= now)):
                delivery.status = "cancelled"
                return
            platform, external_id, attempts = identity.platform, identity.external_id, delivery.attempts
            session.expunge(delivery)
        url = self.link(delivery, row)
        if row and delivery.event_type in WAIT_TYPES.values() and not await self.is_current(row):
            ok, status = False, "cancelled"
        else:
            status = "failed"
            try:
                async with asyncio.timeout(90):
                    if delivery.token and row is not None:
                        ok = await send_request(self, delivery, row, external_id, url)
                    else:
                        body = delivery.text
                        if row is not None and delivery.event_type == "ask_user":
                            request = json.loads(row.request_json)
                            body += "\n\n" + "\n".join(question["question"] for question in request["questions"])
                        if platform == "discord":
                            body = body[:max(0, 1900 - len(url))]
                        ok = await send_notification(platform, external_id, f"{body}\n\n{url}", mode="plain")
            except Exception:
                logger.warning("Notification delivery %s failed.", delivery_id)
                ok = False
        if ok:
            status = "sent"
        elif status == "failed" and attempts < 4:
            status = "pending"
        async with await self.session_factory() as session, session.begin():
            await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.id == delivery_id, NotificationDelivery.attempts == attempts,
            ).values(status=status, due_at=self.clock() + 5 * 2 ** attempts))

    async def recover(self):
        from agent.modules.notifications.recovery import recover_waits

        try:
            await recover_waits(self)
        except Exception:
            logger.exception("Could not import existing pending interruptions.")
