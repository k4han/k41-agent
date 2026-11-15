"""Execute durable subagent jobs without owning an HTTP subscription."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from contextlib import aclosing
from contextvars import Context
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl
from langgraph.types import Command

from agent.modules.agent_runtime.async_subagents.repository import AsyncSubagentRepository
from agent.shared.async_subagents import async_wakeup_references, is_subagent_run, request_key, task_reference

logger = logging.getLogger(__name__)


@dataclass
class ManagedExecution:
    input: Any
    job_id: str
    source: str
    agent_name: str
    workflow: str
    interrupt_id: str = ""
    control: RunControl = field(default_factory=RunControl)


def _run_context(context) -> dict:
    """Persist references and effective restrictions, never live service objects."""
    keys = ("agent_name", "provider", "model", "reasoning_effort", "permission_mode",
            "allowed_tool_names", "allowed_skill_names", "context_compact_threshold",
            "channel_context_trim_threshold", "browser_owner_id")
    if isinstance(context, dict):
        values = {key: context.get(key) for key in keys}
        workspace = context.get("workspace")
    else:
        values = {key: getattr(context, key, None) for key in keys}
        workspace = getattr(context, "workspace", None)
    values["workspace"] = workspace.model_dump() if hasattr(workspace, "model_dump") else (workspace if isinstance(workspace, dict) else None)
    return values


class AsyncSubagentService:
    def __init__(self, container, repository=None):
        self.container = container
        self.repository = repository or AsyncSubagentRepository()
        self._owner = uuid.uuid4().hex
        self._lock = asyncio.Lock()
        self._signal = asyncio.Event()
        self._dispatcher: asyncio.Task | None = None
        self._running: dict[str, tuple[dict, asyncio.Task]] = {}
        self._controls: dict[str, RunControl] = {}
        self._stopping = False
        self._blocked_threads: set[str] = set()
        self._cancelled_sessions: set[str] = set()

    async def start(self):
        if self._dispatcher is None:
            self._stopping = False
            async def dispatch():
                from agent.bootstrap.container import activate_context_container, restore_context_container

                token = activate_context_container(self.container)
                try:
                    await self._dispatch()
                finally:
                    restore_context_container(token)

            self._dispatcher = asyncio.create_task(dispatch(), name="async-subagents", context=Context())

    async def shutdown(self):
        if self._stopping:
            return
        self._stopping = True
        self._signal.set()
        for control in self._controls.values():
            control.request_drain("shutdown")
        if self._dispatcher:
            await self._dispatcher
            self._dispatcher = None
        tasks = [task for _, task in self._running.values()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _validate_target(self, caller: str, target: str):
        if not self.container.agent_catalog_service.validate_call(caller, target):
            raise PermissionError(f"Agent '{caller}' is not allowed to call '{target}'.")

    async def submit(self, *, task: str, sub_agent: str, runtime) -> dict:
        from agent.modules.tools import invocation_context
        from agent.modules.usage import usage_context_from_config

        config = runtime.config
        if is_subagent_run(config):
            raise PermissionError("Subagents cannot delegate to other agents.")
        parent = str(config.get("configurable", {}).get("thread_id") or "")
        if not parent or not config.get("configurable", {}).get("async_subagents_supported"):
            raise PermissionError("Async subagents require a dashboard conversation.")
        if not task.strip():
            raise ValueError("Task must not be empty.")
        caller = runtime.context.agent_name
        self._validate_target(caller, sub_agent)
        context = _run_context(runtime.context)
        context["permission_mode"] = invocation_context(runtime).permission_mode.value
        context["usage_context"] = usage_context_from_config(config).to_dict()
        if self.container._persistence_ready:
            from agent.modules.projects import get_project_workspace_service

            projects = get_project_workspace_service(self.container)
            binding = await projects.resolve_thread(parent.split(":sub:", 1)[0])
            if binding:
                context["workspace"] = binding.model_dump()
        parent_context = dict(context)
        metadata = config.get("metadata", {})
        parent_context["workflow"] = metadata.get("k41_run_workflow") or self.container.agent_catalog_service.get_agent(caller).graph_type
        parent_context["agent_name"] = metadata.get("k41_run_agent_name") or caller
        parent_context["routed_agent_name"] = caller
        parent_context["delegated_agent_name"] = sub_agent
        target = self.container.agent_catalog_service.get_agent(sub_agent)
        context.update(agent_name=sub_agent, workflow=target.graph_type, allowed_tool_names=None)
        messages = (runtime.state or {}).get("messages", [])
        message_id = str(getattr(messages[-1], "id", "") or "") if messages else ""
        call_id = str(runtime.tool_call_id or "")
        if not call_id:
            raise ValueError("A tool call identity is required for durable delegation.")
        key = request_key(parent, str(config.get("configurable", {}).get("checkpoint_ns", "")), message_id, call_id)
        async with self._lock:
            from agent.modules.agent_runtime.active_sessions import current_session_id_var
            if current_session_id_var.get() in self._cancelled_sessions:
                raise ValueError("The originating session was stopped.")
            if self._stopping or any(parent == item or parent.startswith(item + ":sub:") for item in self._blocked_threads):
                raise ValueError("The conversation is stopping.")
            current = asyncio.current_task()
            if current and current.cancelling():
                raise asyncio.CancelledError
            created = await self.repository.create_task(key=key, parent_thread_id=parent, tool_call_id=call_id,
                parent_message_id=message_id, agent_name=sub_agent, task=task.strip(), context=context,
                parent_context=parent_context)
        await self.start()
        self._publish(created)
        self._signal.set()
        return created

    async def update(self, parent: str, task_id: str, instructions: str, key: str) -> dict:
        task = await self.get(parent, task_id, private=True)
        self._validate_target(task["parent_context"].get("routed_agent_name", task["parent_context"]["agent_name"]),
                              task["parent_context"].get("delegated_agent_name", task["agent_name"]))
        if not instructions.strip():
            raise ValueError("Instructions must not be empty.")
        async with self._lock:
            if self._stopping or parent in self._blocked_threads:
                raise ValueError("The conversation is stopping.")
            result = await self.repository.enqueue_update(parent, task_id, instructions.strip(), key)
        self._publish(result)
        self._signal.set()
        return result

    async def get(self, parent: str, task_id: str, *, private=False) -> dict:
        result = await self.repository.get_task(parent, task_id, private=private)
        if result is None:
            raise LookupError("Async task not found.")
        return result

    async def resume(self, parent: str, task_id: str, run_id: str, interrupt_id: str, payload: dict,
                     *, browser_owner_id: str | None = None) -> dict:
        from agent.modules.agent_runtime.runner import (
            _normalize_human_resume_payload, _resolve_agent_name_for_resume, _validate_human_resume_payload,
        )

        normalized = _normalize_human_resume_payload(payload)
        if normalized is None:
            raise ValueError("A human response is required.")
        _validate_human_resume_payload(normalized)
        context = None
        if normalized.action == "browser_handoff":
            task = await self.get(parent, task_id, private=True)
            if not browser_owner_id or task["context"].get("browser_owner_id") != browser_owner_id:
                raise PermissionError("This browser belongs to another authenticated account.")
        if normalized.action == "approve":
            task = await self.get(parent, task_id, private=True)
            if task["status"] == "waiting":
                selected = _resolve_agent_name_for_resume(self.container.agent_catalog_service,
                    task["context"]["agent_name"], normalized, source_agent_name=task["context"]["agent_name"])
                context = {**task["context"], "agent_name": selected,
                           "workflow": self.container.agent_catalog_service.get_agent(selected).graph_type}
        async with self._lock:
            result = await self.repository.resume_task(parent, task_id, run_id, interrupt_id,
                normalized.model_dump(exclude_none=True), context=context)
        self._publish(result)
        self._signal.set()
        return result

    async def cancel(self, parent: str, task_id: str) -> dict:
        task = await self.get(parent, task_id)
        await self.cancel_tree(task["thread_id"])
        return await self.get(parent, task_id)

    async def cancel_tree(self, thread_id: str, *, delete=False) -> bool:
        """Fence admission and commit cancellation before signalling live runs."""
        self._blocked_threads.add(thread_id)
        try:
            async with self._lock:
                had_work = await self.repository.has_work(thread_id)
                for session in self.container.active_session_registry.list_active():
                    if session["thread_id"] == thread_id or session["thread_id"].startswith(thread_id + ":sub:"):
                        self._cancelled_sessions.add(session["session_id"])
                tasks = await self.repository.cancel_scope(thread_id, delete=delete)
                for job, running in list(self._running.values()):
                    if job["thread_id"] == thread_id or job["thread_id"].startswith(thread_id + ":sub:"):
                        session = await self.container.chat_stream_manager.get_session(job["thread_id"])
                        if session:
                            session.cancel(force=True)
                        running.cancel()
                children = {task["thread_id"] for task in tasks}
                for child in children:
                    self.container.active_session_registry.cancel_by_thread(child)
                for task in tasks:
                    self._publish(task)
            from agent.modules.browser import release_delegated_browser

            for child in children:
                await release_delegated_browser(child)
            from agent.modules.browser import finish_thread_browser
            await finish_thread_browser(thread_id, "cancelled")
            return bool(tasks) or had_work
        finally:
            self._blocked_threads.discard(thread_id)

    def _publish(self, task: dict):
        if task.get("execution_kind") == "scheduled":
            self.container.active_session_registry.publish("scheduler_run_updated", task)
            return
        self.container.active_session_registry.publish("subagent_task_updated", task_reference(task))

    async def _finish_job(self, job, status, **values):
        result = await self.repository.finish(job, status, **values)
        if result and result.get("execution_kind") == "scheduled":
            from agent.modules.scheduler import notify_run
            await notify_run(result)
        return result

    async def model_updates(self, state: dict, config: dict) -> dict:
        if self._stopping or not config.get("configurable", {}).get("async_subagents_supported"):
            return {}
        parent = str(config["configurable"]["thread_id"])
        cursors = dict(state.get("async_event_cursors", {}))
        messages, references = [], {}
        events = [event for event in await self.repository.pending_notifications(parent)
                  if event["version"] > cursors.get(event["task_id"], 0)]
        wakeup_id = (config.get("metadata", {}).get("k41_execution_id")
                     or (f"async-event:{events[0]['event_id']}" if events else ""))
        for event in events:
            task_id, version = event["task_id"], event["version"]
            if version <= cursors.get(task_id, 0):
                continue
            payload = event["payload"]
            status = payload["event_status"]
            detail = payload.get("result") or payload.get("error") or "Waiting for the user."
            if status == "waiting":
                detail = "Waiting for user input or approval in the dashboard. Never answer or approve on the user's behalf."
            messages.append(HumanMessage(id=f"async:{event['event_id']}",
                content=f"[Async subagent {payload['agent_name']}; task_id={task_id}; run_id={payload['run_id']}; "
                        f"event={status}; current_status={payload['status']}]\n{detail[:20000]}",
                additional_kwargs={"source": "async_subagent", "async_task_id": task_id,
                                   "async_event_id": event["event_id"], "async_agent_name": payload["agent_name"],
                                   "async_status": status, "async_wakeup_id": wakeup_id}))
            cursors[task_id] = version
            references[task_id] = task_reference(payload)
        if messages:
            from langgraph.config import get_stream_writer

            try:
                writer = get_stream_writer()
            except RuntimeError:
                writer = None
            if writer is not None:
                reference = next(iter(async_wakeup_references(messages).values()))
                writer({"type": "async_wakeup", **reference})
        return {"messages": messages, "async_tasks": references, "async_event_cursors": cursors} if messages else {}

    async def reconcile(self, thread_id: str, graph=None, config=None):
        if graph is None:
            from agent.modules.workflows import get_workflow_graph, graph_name_for_thread

            graph = get_workflow_graph(await graph_name_for_thread(thread_id))
        snapshot = await graph.aget_state(config or {"configurable": {"thread_id": thread_id}})
        await self.repository.acknowledge(thread_id, (snapshot.values or {}).get("async_event_cursors", {}))
        self._signal.set()

    async def _dispatch(self):
        while not self._stopping:
            try:
                for job_id, (job, running) in list(self._running.items()):
                    if running.done():
                        self._running.pop(job_id)
                        if not running.cancelled() and running.exception():
                            logger.error("Async execution %s failed: %s", job_id, running.exception())
                    else:
                        await self.repository.heartbeat(job)
                await self.repository.prepare_wakeups()
                active = Counter(job["root_thread_id"] for job, _ in self._running.values()
                    if job["task_id"] or job["kind"] == "scheduled")
                maximum = max(1, self.container.config_service.get_int("subagents.max_concurrency", 8))
                per_root = max(1, self.container.config_service.get_int("subagents.max_concurrency_per_thread", 4))
                children = sum(active.values())
                for candidate in await self.repository.candidates():
                    if candidate["job_id"] in self._running:
                        continue
                    if (candidate["task_id"] or candidate["kind"] == "scheduled") and (children >= maximum or active[candidate["root_thread_id"]] >= per_root):
                        continue
                    if candidate["kind"] == "wakeup" and not await self._parent_ready(candidate):
                        continue
                    job = await self.repository.claim(candidate["job_id"], self._owner)
                    if job:
                        if job["task_id"]:
                            self._publish(await self.repository.get_task_for_execution(job["task_id"]))
                        running = asyncio.create_task(self._execute(job), name=f"async-job-{job['job_id']}")
                        self._running[job["job_id"]] = (job, running)
                        if job["task_id"] or job["kind"] == "scheduled":
                            children += 1
                            active[job["root_thread_id"]] += 1
                self._signal.clear()
                try:
                    await asyncio.wait_for(self._signal.wait(), timeout=1)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Async subagent dispatcher will retry after a storage error.")
                self._signal.clear()
                if not self._stopping:
                    try:
                        await asyncio.wait_for(self._signal.wait(), timeout=1)
                    except TimeoutError:
                        pass

    async def _parent_ready(self, job: dict) -> bool:
        session = await self.container.chat_stream_manager.get_session(job["thread_id"])
        if session and not session.done:
            return False
        if any(item["thread_id"] == job["thread_id"] for item in self.container.active_session_registry.list_active()):
            return False
        from agent.modules.workflows import get_workflow_graph

        try:
            graph = get_workflow_graph(job["context"]["workflow"])
            snapshot = await graph.aget_state({"configurable": {"thread_id": job["thread_id"]}})
        except (KeyError, ValueError):
            # Let the managed stream persist and display a removed graph's error.
            return True
        return not any(task.interrupts for task in snapshot.tasks)

    async def _execute(self, job: dict):
        async def run(**params):
            from agent.modules.agent_runtime.runner import track_active_session

            with track_active_session(job["thread_id"], job["context"]["agent_name"],
                                      usage_context=job["context"].get("usage_context")):
                try:
                    async with aclosing(self._job_stream(job, params.get("_run_id"))) as events:
                        async for event in events:
                            yield event
                except (asyncio.CancelledError, GraphDrained):
                    raise
                except Exception as exc:
                    result = await self._finish_job(job, "failed", error=str(exc))
                    if result:
                        self._publish(result)
                    raise

        session = None
        try:
            session = await self.container.chat_stream_manager.start_if_idle(job["thread_id"], {}, run_fn=run)
            if session is None:
                await self.repository.heartbeat(job, release=True)
                return
            await asyncio.shield(session.task)
        except asyncio.CancelledError:
            if session and not session.done:
                session.cancel(force=True)
                await asyncio.gather(session.task, return_exceptions=True)
            await self.repository.heartbeat(job, release=True)
            raise
        except Exception as exc:
            result = await self._finish_job(job, "failed", error=str(exc))
            if result:
                self._publish(result)
            raise
        finally:
            self._signal.set()

    async def _job_stream(self, job: dict, stream_run_id: str | None):
        from agent.modules.agent_runtime.protocol import application_event, run_agent_protocol_stream
        from agent.modules.browser import finish_thread_browser
        from agent.modules.workflows import get_workflow_graph

        context = {key: value for key, value in job["context"].items()
                   if key not in {"routed_agent_name", "delegated_agent_name", "_scheduled"}}
        graph = get_workflow_graph(context["workflow"])
        config = {"configurable": {"thread_id": job["thread_id"]}}
        snapshot = await graph.aget_state(config)
        metadata = snapshot.metadata or {}
        if job["kind"] == "wakeup" and isinstance(metadata.get("k41_run_context"), dict):
            context = {**metadata["k41_run_context"], "workflow": context["workflow"]}
            target = (snapshot.values or {}).get("target_agent")
            routed_agent = getattr(target, "name", None) or job["context"].get("routed_agent_name")
            job["context"] = {**context, "routed_agent_name": routed_agent}
            await self.repository.refresh_context(job, job["context"])
        same_job = metadata.get("k41_execution_id") == job["job_id"]
        pending = [interrupt for task in snapshot.tasks for interrupt in task.interrupts]
        if job["kind"] == "wakeup" and pending and not same_job:
            await self.repository.heartbeat(job, release=True)
            return
        resume_payload, interrupt_id = None, ""
        if same_job and not snapshot.next and not pending:
            text = self._saved_result(snapshot)
            result = await self._finish_job(job, "completed", result=text,
                checkpoint_id=snapshot.config.get("configurable", {}).get("checkpoint_id"))
            if result:
                self._publish(result)
            await self.reconcile(job["thread_id"], graph, config)
            return
        if same_job and pending:
            resume = job.get("resume")
            if resume and resume["interrupt_id"] in {item.id for item in pending}:
                interrupt_id, resume_payload = resume["interrupt_id"], resume["payload"]
                input_data = Command(resume={interrupt_id: resume_payload})
            else:
                result = await self._finish_job(job, "waiting", interrupts=self._interrupts(snapshot),
                    checkpoint_id=snapshot.config.get("configurable", {}).get("checkpoint_id"))
                if result:
                    self._publish(result)
                return
        elif same_job:
            input_data = None
        elif job["kind"] == "wakeup":
            updates = await self.model_updates(snapshot.values or {}, {
                "configurable": {"thread_id": job["thread_id"], "async_subagents_supported": True},
                "metadata": {"k41_execution_id": job["job_id"]}})
            if not updates and not snapshot.next:
                await self._finish_job(job, "completed")
                return
            input_data = updates
            if snapshot.next:
                input_data = Command(update=updates) if updates else None
        else:
            input_data = {"messages": [HumanMessage(content=job["input"]["text"], id=f"async-input:{job['job_id']}")]}
        wakeup = None
        if job["kind"] == "wakeup":
            incoming = input_data.update if isinstance(input_data, Command) else input_data
            messages = [*(snapshot.values or {}).get("messages", []),
                        *(incoming.get("messages", []) if isinstance(incoming, dict) else [])]
            wakeup = next((value for value in async_wakeup_references(messages).values()
                           if value["wakeup_id"] == job["job_id"]), None)
        execution = ManagedExecution(input_data, job["job_id"], job["kind"], context["agent_name"],
                                     context["workflow"], interrupt_id=interrupt_id)
        self._controls[job["job_id"]] = execution.control
        checkpoint_metadata = {"k41_execution_id": job["job_id"]}
        if job["kind"] == "scheduled":
            checkpoint_metadata["k41_scheduled_run_id"] = job["job_id"]
        if job["task_id"]:
            task = await self.repository.get_task_for_execution(job["task_id"])
            checkpoint_metadata.update(k41_parent_thread_id=task["parent_thread_id"],
                k41_parent_tool_call_id=task["tool_call_id"], k41_parent_message_id=task["parent_message_id"],
                k41_delegation_id=task["task_id"], k41_async_task_id=task["task_id"], k41_agent_name=task["agent_name"])
        final = ""
        status = "running"
        try:
            timeout = max(0, self.container.config_service.get_int("subagents.run_timeout_seconds", 0))
            async with asyncio.timeout(timeout or None), aclosing(run_agent_protocol_stream(
                **context, user_input=job["input"].get("text", ""), thread_id=job["thread_id"], resume=same_job,
                resume_payload=resume_payload, _checkpoint_metadata=checkpoint_metadata,
                _execution=execution, _run_id=stream_run_id,
                _async_wakeup=wakeup,
                _async_notification_agent=job["context"].get("routed_agent_name") if job["kind"] == "wakeup" else None,
            )) as events:
                async for event in events:
                    projected = application_event(event)
                    if projected and projected.get("type") == "final":
                        final = str(projected.get("content") or "")
                    yield event
            graph = get_workflow_graph(execution.workflow)
            snapshot = await graph.aget_state(config)
            interrupts = self._interrupts(snapshot)
            status = "waiting" if interrupts else "completed"
            context.update(agent_name=execution.agent_name, workflow=execution.workflow)
            result = await self._finish_job(job, status, result=final or self._saved_result(snapshot),
                interrupts=interrupts, checkpoint_id=snapshot.config.get("configurable", {}).get("checkpoint_id"),
                context=context)
            if result:
                self._publish(result)
            await self.reconcile(job["thread_id"], graph, config)
        except GraphDrained:
            await self.repository.heartbeat(job, release=True)
        except asyncio.CancelledError:
            await self.repository.heartbeat(job, release=True)
            raise
        except TimeoutError:
            status = "timed_out"
            result = await self._finish_job(job, status, error="Async task execution timed out.")
            if result:
                self._publish(result)
            raise
        except Exception as exc:
            status = "failed"
            result = await self._finish_job(job, status, error=str(exc))
            if result:
                self._publish(result)
            raise
        finally:
            self._controls.pop(job["job_id"], None)
            if status != "waiting":
                await finish_thread_browser(job["thread_id"], "interrupted" if status == "running" else status)

    @staticmethod
    def _saved_result(snapshot) -> str:
        from langchain_core.messages import AIMessage
        from agent.shared.infrastructure.parsing import extract_final_text_content

        for message in reversed((snapshot.values or {}).get("messages", [])):
            if isinstance(message, AIMessage) and not message.tool_calls:
                return extract_final_text_content(message.content)
        return ""

    @staticmethod
    def _interrupts(snapshot) -> list[dict]:
        return [{"id": item.id, "value": item.value} for task in snapshot.tasks for item in task.interrupts]
