"""Scheduled runs backed by the managed execution queue and graph checkpoints."""

import asyncio
import logging
import time

from sqlalchemy import select, update

from agent.modules.agent_runtime import SubagentExecutionRecord as Execution, encode, job_dict
from agent.modules.scheduler.history import MAX_STORED_TEXT_LEN
from agent.modules.scheduler.models import SchedulerJobRun
from agent.shared.async_subagents import LIVE_STATUSES, request_key
from agent.shared.infrastructure.db import utcnow

logger = logging.getLogger(__name__)


async def admit_run(service, *, run_id, thread_id, job_id, task, platform, user_id,
                    trigger_source, workspace, browser_owner_id, executor):
    """Persist admission and history together; overlapping ticks become skipped runs."""
    card = service.container.agent_catalog_service.get_agent("scheduler-executor")
    if card is None:
        raise ValueError("The scheduler-executor agent is unavailable.")
    context = {"agent_name": "scheduler-executor", "workflow": card.graph_type,
        "workspace": workspace.model_dump(), "browser_owner_id": browser_owner_id,
        "provider": executor["provider"] or None, "model": executor["model"] or None,
        "usage_context": {"platform": "bg", "user_id": user_id, "channel_id": run_id},
        "_scheduled": {"job_id": job_id, "task": task, "platform": platform, "user_id": user_id}}
    async with service._lock:
        async with await service.repository._session_factory() as session, session.begin():
            overlap = await session.scalar(select(SchedulerJobRun.run_id).where(
                SchedulerJobRun.job_id == job_id, SchedulerJobRun.status.in_(LIVE_STATUSES)).limit(1)) if job_id else None
            status = "skipped" if overlap else "pending"
            session.add(SchedulerJobRun(run_id=run_id, job_id=job_id, task=task, platform=platform,
                user_id=user_id, trigger_source=trigger_source, thread_id=thread_id,
                agent_name="scheduler-executor", provider=executor["provider"], model=executor["model"],
                status=status, error=f"Skipped because run {overlap} is still active." if overlap else "",
                finished_at=utcnow() if overlap else None))
            if not overlap:
                now = time.time()
                session.add(Execution(job_id=run_id, request_key=request_key("scheduled", run_id), task_id="",
                    thread_id=thread_id, root_thread_id=thread_id, kind="scheduled", status="pending",
                    context_json=encode(context), input_json=encode({"text": task}), created_at=now, updated_at=now))
    if not overlap:
        await service.start()
        service._signal.set()
    else:
        await _prune_history()
    service.container.active_session_registry.publish("scheduler_run_updated", {"run_id": run_id, "thread_id": thread_id, "status": status})
    return {"run_id": run_id, "thread_id": thread_id, "status": status}


async def settle_run(session, row, status, *, result="", error="", context=None):
    """Commit queue state and its visible history in the same transaction."""
    if context:
        saved = job_dict(row)["context"]
        row.context_json = encode({**context, "_scheduled": saved["_scheduled"]})
    await session.execute(update(SchedulerJobRun).where(SchedulerJobRun.run_id == row.job_id).values(
        status=status, response=result[:MAX_STORED_TEXT_LEN], error=error[:MAX_STORED_TEXT_LEN],
        finished_at=None if status in LIVE_STATUSES else utcnow()))
    return {"execution_kind": "scheduled", "run_id": row.job_id, "thread_id": row.thread_id,
            "status": status, "result": result, "error": error, **job_dict(row)["context"]["_scheduled"]}


async def notify_run(value):
    """Deliver the outcome after persistence; notification failure cannot undo a run."""
    if value["status"] not in {"completed", "failed", "timed_out"}:
        return
    from agent.modules.scheduler.service import _send_notification, _task_title

    content = value["result"] if value["status"] == "completed" else f"Error: {value['error']}"
    try:
        await _send_notification(value["platform"], value["user_id"],
            f"**Scheduled task:** {_task_title(value['task'])}\n\n{content}", mode="markdown")
    except Exception:
        logger.warning("Could not deliver scheduled run %s.", value["run_id"], exc_info=True)
    await _prune_history()


async def _prune_history():
    from agent.modules.scheduler.history import prune_runs
    try:
        await prune_runs()
    except Exception:
        logger.warning("Could not prune scheduled run history.", exc_info=True)


async def get_run(service, run_id, owner_id):
    async with await service.repository._session_factory() as session:
        row = await session.scalar(select(Execution).where(Execution.job_id == run_id, Execution.kind == "scheduled"))
        if row is None or job_dict(row)["context"].get("browser_owner_id") != owner_id:
            raise LookupError("Scheduled execution not found.")
        return job_dict(row)


async def waiting_run(service, thread_id, owner_id, *, strict=False):
    async with await service.repository._session_factory() as session:
        row = await session.scalar(select(Execution).where(Execution.thread_id == thread_id,
            Execution.kind == "scheduled", Execution.status.in_(LIVE_STATUSES)))
        if row is None:
            return None
        if job_dict(row)["context"].get("browser_owner_id") != owner_id:
            if strict:
                raise LookupError("Scheduled execution not found.")
            return None
        job = job_dict(row)
    value = {"run_id": job["job_id"], "status": job["status"]}
    if job["status"] == "waiting":
        from agent.modules.workflows import get_workflow_graph
        snapshot = await get_workflow_graph(job["context"]["workflow"]).aget_state(
            {"configurable": {"thread_id": thread_id}})
        value["interrupts"] = service._interrupts(snapshot)
    return value


async def resume_run(service, run_id, interrupt_id, payload, owner_id):
    from agent.modules.agent_runtime import _normalize_human_resume_payload, _validate_human_resume_payload
    from agent.modules.workflows import get_workflow_graph

    normalized = _normalize_human_resume_payload(payload)
    if normalized is None:
        raise ValueError("A human response is required.")
    _validate_human_resume_payload(normalized)
    payload = normalized.model_dump(exclude_none=True)
    resume = {"interrupt_id": interrupt_id, "payload": payload}
    async with service._lock:
        job = await get_run(service, run_id, owner_id)
        if job["status"] in {"pending", "running"} and job["resume"] == resume:
            return {"run_id": run_id, "status": job["status"]}
        if job["status"] != "waiting":
            raise ValueError("The scheduled run is no longer waiting.")
        graph = get_workflow_graph(job["context"]["workflow"])
        snapshot = await graph.aget_state({"configurable": {"thread_id": job["thread_id"]}})
        if (snapshot.metadata or {}).get("k41_execution_id") != run_id:
            raise ValueError("The checkpoint no longer belongs to this scheduled run.")
        pending = next((item for item in service._interrupts(snapshot) if item["id"] == interrupt_id), None)
        if pending is None:
            raise ValueError("The interrupt no longer belongs to the waiting scheduled run.")
        value = pending["value"]
        browser_request = isinstance(value, dict) and value.get("type") == "browser_handoff_request"
        if browser_request and (payload["action"] != "browser_handoff" or payload.get("handoff_id") != value.get("handoff_id")):
            raise ValueError("The response does not match the waiting browser handoff.")
        if not browser_request and payload["action"] == "browser_handoff":
            raise ValueError("The interrupt is not a browser handoff.")
        expected = {"permission_request": {"permission"}, "acp_permission_request": {"acp_permission"},
                    "user_input_request": {"answer"}, "plan_review": {"approve", "revise"}}
        if isinstance(value, dict) and value.get("type") in expected and payload["action"] not in expected[value["type"]]:
            raise ValueError("The response type does not match the pending request.")
        context = job["context"]
        if normalized.action == "approve":
            from agent.modules.agent_runtime import _resolve_agent_name_for_resume
            selected = _resolve_agent_name_for_resume(service.container.agent_catalog_service,
                context["agent_name"], normalized, source_agent_name=context["agent_name"])
            context = {**context, "agent_name": selected,
                "workflow": service.container.agent_catalog_service.get_agent(selected).graph_type}
        async with await service.repository._session_factory() as session, session.begin():
            updated = await session.execute(update(Execution).where(Execution.job_id == run_id,
                Execution.status == "waiting").values(status="pending", resume_json=encode(resume),
                    context_json=encode(context), lease_until=0))
            if updated.rowcount != 1:
                raise ValueError("The scheduled run is no longer waiting.")
            await session.execute(update(SchedulerJobRun).where(SchedulerJobRun.run_id == run_id)
                .values(status="pending", finished_at=None))
    await service.start()
    service._signal.set()
    return {"run_id": run_id, "status": "pending"}


async def cancel_run(service, run_id, owner_id):
    job = await get_run(service, run_id, owner_id)
    if job["status"] == "cancelled":
        return {"run_id": run_id, "status": "cancelled"}
    if job["status"] not in LIVE_STATUSES:
        raise ValueError("Only active scheduled runs can be cancelled.")
    async with service._lock:
        async with await service.repository._session_factory() as session, session.begin():
            updated = await session.execute(update(Execution).where(Execution.job_id == run_id,
                Execution.status.in_(LIVE_STATUSES)).values(status="cancelled", lease_until=0))
            if updated.rowcount:
                await session.execute(update(SchedulerJobRun).where(SchedulerJobRun.run_id == run_id)
                    .values(status="cancelled", error="Task was cancelled.", finished_at=utcnow()))
        if not updated.rowcount:
            raise ValueError("The scheduled run is no longer active.")
        running = service._running.get(run_id)
        if running:
            running[1].cancel()
    if running:
        await asyncio.gather(running[1], return_exceptions=True)
    from agent.modules.browser import finish_thread_browser

    await service.cancel_tree(job["thread_id"])
    await finish_thread_browser(job["thread_id"], "cancelled")
    service.container.active_session_registry.publish("scheduler_run_updated", {"run_id": run_id, "status": "cancelled"})
    return {"run_id": run_id, "status": "cancelled"}
