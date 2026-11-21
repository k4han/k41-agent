"""Import pending checkpoints created before notification tracking was available."""

import logging

from sqlalchemy import select

from agent.modules.agent_runtime import SubagentExecutionRecord as Execution, SubagentTaskRecord as Task
from agent.modules.agent_runtime import job_dict
from agent.modules.conversations import ConversationThread
from agent.modules.notifications.interruption import pending_interrupts
from agent.modules.workflows import list_checkpoint_summaries

logger = logging.getLogger(__name__)


async def recover_waits(manager):
    from agent.modules.admin_auth import get_admin_auth_service
    from agent.modules.workflows import get_workflow_graph
    from agent.modules.workspaces import workspace_ref_for_thread
    from agent.shared.thread_ids import resolve_thread_id

    async with await manager.session_factory() as session:
        waiting = (await session.scalars(select(Execution).where(Execution.status == "waiting"))).all()
        runs = [job_dict(row) for row in waiting]
        threads = {row.thread_id: row for row in (await session.scalars(select(ConversationThread))).all()}
        tasks = {row.task_id: row for row in (await session.scalars(select(Task).where(
            Task.status == "waiting", Task.deleted_at.is_(None)))).all()}
    for run in runs:
        context = dict(run["context"])
        if run["task_id"]:
            task = tasks.get(run["task_id"])
            if task is None or task.active_job_id != run["job_id"]:
                continue
            context.update(_notification_task_id=task.task_id, _notification_parent=task.parent_thread_id)
        await _import(manager, run["thread_id"], run["root_thread_id"], context, job_id=run["job_id"])

    admin = await get_admin_auth_service(manager.container).get_admin()
    for summary in await list_checkpoint_summaries(manager.container.checkpointer) or []:
        thread_id = resolve_thread_id(summary["thread_id"])
        thread = threads.get(thread_id)
        if thread is None or thread.deleted_at or thread.kind != "user" or ":sub:" in thread_id:
            continue
        card = manager.container.agent_catalog_service.get_agent(thread.agent_name or "default")
        if card is None:
            continue
        try:
            graph = get_workflow_graph(card.graph_type)
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            metadata = snapshot.metadata or {}
            if metadata.get("k41_execution_id") or not pending_interrupts(snapshot):
                continue
            context = metadata.get("k41_notification_context") or metadata.get("k41_run_context")
            if not context:
                browser = await manager.container.browser_service.repository.binding(thread_id)
                workspace = await workspace_ref_for_thread(thread_id, include_default=False)
                context = await manager.run_context({
                    "agent_name": card.name, "provider": thread.provider or None, "model": thread.model or None,
                    "workspace": workspace, "browser_owner_id": (
                        browser["owner_id"] if browser else f"admin:{admin.id}" if admin and thread.platform == "api" else None),
                }, card.graph_type, {"platform": thread.platform, "user_id": thread.user_id,
                                     "channel_id": thread.channel_id})
            await _import(manager, thread_id, thread_id, {"workflow": card.graph_type, **context}, snapshot=snapshot)
        except Exception:
            logger.warning("Could not recover notification state for %s.", thread_id, exc_info=True)


async def _import(manager, thread_id, root_thread_id, context, *, job_id="", snapshot=None):
    from agent.modules.workflows import get_workflow_graph

    if snapshot is None:
        graph = get_workflow_graph(context["workflow"])
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    if job_id and (snapshot.metadata or {}).get("k41_execution_id") != job_id:
        return
    async with await manager.session_factory() as session, session.begin():
        await manager.register(session, thread_id=thread_id, root_thread_id=root_thread_id,
                               checkpoint_id=snapshot.config["configurable"].get("checkpoint_id"),
                               interrupts=pending_interrupts(snapshot), context=context, job_id=job_id)
