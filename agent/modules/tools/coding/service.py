"""Container-scoped coding runtime and single settlement boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.modules.tools.coding.files import FileService
from agent.modules.tools.coding.engine import CodingEngine
from agent.modules.tools.coding.names import PROCESS_TOOLS
from agent.modules.tools.coding.models import CodingError, CodingInvocation, InvocationContext, ResultError, ToolDefinition, ToolResult
from agent.modules.tools.coding.permissions import Permissions
from agent.modules.tools.coding.processes import ProcessManager, resolve_shell
from agent.modules.tools.coding.storage import OutputStore, digest

logger = logging.getLogger(__name__)


class CodingService(CodingEngine):
    def __init__(self, root: Path, *, shell: str | None = None) -> None:
        self.storage = OutputStore(root, result_loader=ToolResult.model_validate)
        self.permissions = Permissions(self.storage)
        from agent.modules.skills import invalidate_repository_skills_for_path
        self.files = FileService(self.permissions, on_change=invalidate_repository_skills_for_path)
        self.processes = ProcessManager(self.storage)
        self.shell = resolve_shell(shell)
        from agent.modules.tools.coding.remote import RemoteCodingRuntime
        self.remote = RemoteCodingRuntime(self)
        self.processes.remote = self.remote
        self.invocation_locks: dict[str, asyncio.Lock] = {}
        self.project_watchers: set[asyncio.Task] = set()
        self.storage.cleanup()
        self.storage.start_cleanup()

    async def prepare(self, name: str, values: dict[str, Any], context: InvocationContext) -> None:
        if context.backend != "local":
            await self.remote.prepare(name, values, context)
        else:
            await super().prepare(name, values, context)
        if name in {"write", "edit", "bash", "write_process_input"} and context.project_workspace_id:
            from agent.modules.projects import get_project_workspace_service
            await get_project_workspace_service().assert_writable(context)

    async def execute(self, name: str, values: dict[str, Any], context: InvocationContext) -> ToolResult:
        if context.backend != "local":
            result = await self.remote.execute(name, values, context)
        else:
            result = await super().execute(name, values, context)
            result = ToolResult.model_validate(result.to_dict())
        if result.status == "running" and result.data.get("process_id"):
            if not context.project_workspace_id:
                from agent.bootstrap.container import get_active_container
                container = get_active_container()
                if container is None or not container._persistence_ready:
                    return result
            from agent.modules.projects import get_project_workspace_service
            projects = get_project_workspace_service()
            owner = context.project_workspace_id
            if owner is None and context.thread_id and projects.container._persistence_ready:
                from agent.modules.workspaces import thread_storage_key
                try:
                    rows, _ = await projects.host_mirrors.thread_status(context.thread_id)
                    owner = -int(thread_storage_key(rows))
                except KeyError:
                    pass
            if owner is None:
                return result
            process_id = result.data["process_id"]
            process_key = (owner, process_id)
            if process_key not in projects.running_processes:
                projects.running_processes.add(process_key)
                task = asyncio.create_task(self._watch_project_process(context, process_id, owner))
                self.project_watchers.add(task)
                task.add_done_callback(self.project_watchers.discard)
        return result

    async def _watch_project_process(self, context, process_id, owner=None):
        try:
            if context.backend == "local":
                await self.processes.get(context, process_id).finished.wait()
            else:
                client = await self.remote.client(context)
                attempt = 0
                while True:
                    response = await client.call(self.remote.request("process_state", "", {"process_id": process_id}, context))
                    if response["finished"]:
                        break
                    await asyncio.sleep((5, 15, 60)[min(attempt, 2)])
                    attempt += 1
            from agent.modules.projects import get_project_workspace_service
            projects = get_project_workspace_service()
            if context.project_workspace_id:
                await projects.checkpoint(context.project_id, context.project_workspace_id, generation=context.runtime_generation)
            else:
                rows, _ = await projects.host_mirrors.thread_status(context.thread_id)
                ref = projects.host_mirrors.thread_ref(rows[0])
                if ref.backend == context.backend and ref.locator == context.locator:
                    await projects.host_mirrors._poll_threads(rows, force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Could not save completed project process workspace=%s", context.project_workspace_id, exc_info=True)
        finally:
            from agent.modules.projects import get_project_workspace_service
            get_project_workspace_service().running_processes.discard((owner or context.project_workspace_id, process_id))

    async def _execute_project_operation(self, definition, parsed, context, fingerprint):
        from agent.modules.projects import get_project_workspace_service
        projects = get_project_workspace_service()
        async with projects.mutation(context.project_id, context.project_workspace_id, generation=context.runtime_generation):
            try:
                result = await definition.execute(parsed, context, self)
                result = definition.output_schema.model_validate(result)
            except (CodingError, OSError, ValueError) as exc:
                result = self.failure(exc)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(projects._checkpoint(context.project_id, context.project_workspace_id,
                                                               context.runtime_generation))
                except Exception:
                    logger.warning("Interrupted project operation has unsaved changes.")
                raise
            # Settle execution before saving, so a storage failure cannot replay effects.
            result.data["persistence"] = {"status": "dirty", "saved": False}
            self.storage.complete(context, fingerprint, result)
            return await projects.persist_result(context, result)

    async def _resolve_context(self, name: str, context: InvocationContext) -> InvocationContext:
        from agent.modules.projects import get_project_workspace_service
        return await get_project_workspace_service().resolve_context(
            context, allow_unready=name in {"read", "list_dir", "glob", "grep", "read_process_output", "stop_process"})

    async def _persist_thread_result(self, context, result):
        from agent.bootstrap.container import get_active_container
        container = get_active_container()
        if container is None or not container._persistence_ready:
            return
        from agent.modules.projects import get_project_workspace_service
        projects = get_project_workspace_service(container)
        try:
            rows, _ = await projects.host_mirrors.thread_status(context.thread_id)
        except KeyError:
            return
        try:
            ref = projects.host_mirrors.thread_ref(rows[0])
            if (ref.backend, ref.locator) != (context.backend, context.locator):
                raise CodingError("stale_runtime", "Execution runtime changed before checkpointing.")
            from agent.modules.workspaces import thread_storage_key
            key = thread_storage_key(rows)
            async with projects.host_mirrors.thread_locks.setdefault(key, asyncio.Lock()):
                rows = await projects.host_mirrors._refresh_threads(rows, ref)
                ref = ref.model_copy(deep=True)
                ref.metadata["_require_running"] = True
                result.data["persistence"] = await projects.host_mirrors.checkpoint_thread(rows, ref)
        except Exception as exc:
            result.data["persistence"] = {"saved": False, "status": "error", "message": str(exc)}
            result.warnings.append("Execution finished, but thread changes could not be checkpointed. Retry saving before retiring the runtime.")

    async def prepare_batch(self, invocations: list[CodingInvocation]) -> ToolResult | None:
        """Authorize every operation before a batch can execute any effects."""
        try:
            remote_invocations = []
            for invocation in invocations:
                definition, args, context = invocation.definition, invocation.args, invocation.context
                parsed = definition.input_schema.model_validate(args)
                context = await self._resolve_context(definition.name, context)
                if definition.name != "read" and context.tool_call_id and self.storage.journal_path(context).exists():
                    self.storage.begin(context, self.fingerprint(definition.name, parsed.model_dump()))
                    continue
                if context.backend == "local":
                    await self.prepare(definition.name, parsed.model_dump(), context)
                else:
                    if definition.name in {"write", "edit", "bash", "write_process_input"} and context.project_workspace_id:
                        from agent.modules.projects import get_project_workspace_service
                        await get_project_workspace_service().assert_writable(context)
                    remote_invocations.append((definition.name, parsed.model_dump(), context))
            await self.remote.prepare_batch(remote_invocations)
        except (CodingError, ValidationError, OSError, ValueError, RuntimeError) as exc:
            result = self.failure(exc)
            result.data["batch_stopped_before_execution"] = True
            result.content = f"[error] Batch stopped before execution: {exc}"
            return result
        return None

    @staticmethod
    def fingerprint(name: str, args: dict[str, Any]) -> str:
        return digest(json.dumps([name, args], sort_keys=True, ensure_ascii=True))

    async def invoke(self, definition: ToolDefinition, args: dict[str, Any], context: InvocationContext) -> ToolResult:
        from agent.modules.tools.coding.remote import coding_authorization_scope

        with coding_authorization_scope():
            return await self._invoke(definition, args, context)

    async def _invoke(self, definition: ToolDefinition, args: dict[str, Any], context: InvocationContext) -> ToolResult:
        try:
            context = await self._resolve_context(definition.name, context)
        except (CodingError, OSError, ValueError, RuntimeError) as exc:
            return self.failure(exc)
        from agent.bootstrap.container import get_active_container
        from agent.modules.projects import storage_timestamp
        from agent.shared.infrastructure.db.base import utcnow
        container = get_active_container()
        projects = getattr(container, "_project_workspace_service", None) if container is not None else None
        if projects is not None:
            projects.metrics.last_user_activity = storage_timestamp(utcnow())
        started = time.monotonic()
        # Reads must observe the current file, including when a call ID is reused.
        use_journal = definition.name != "read"
        fingerprint = self.fingerprint(definition.name, args)
        key = f"{context.owner}\0{context.message_id}\0{context.tool_call_id}" if context.tool_call_id else os.urandom(16).hex()
        lock = self.invocation_locks.setdefault(key, asyncio.Lock())
        journal_started = False
        result = None
        try:
            self.storage.owner_dir(context)
            self.storage.start_cleanup()
            parsed = definition.input_schema.model_validate(args)
            values = parsed.model_dump()
            fingerprint = self.fingerprint(definition.name, values)
            async with lock:
                # Completed settlements are returned before authorization so
                # replay cannot consume a resume value intended for another call.
                if use_journal and context.tool_call_id:
                    path = self.storage.journal_path(context)
                    if path.exists():
                        result = self.storage.begin(context, fingerprint)
                        result = self.model_result(definition.name, result, context)
                        return result
                await self.prepare(definition.name, values, context)
                if use_journal:
                    cached = self.storage.begin(context, fingerprint)
                    if cached is not None:
                        result = self.model_result(definition.name, cached, context)
                        return result
                    journal_started = True
                try:
                    if context.project_workspace_id and definition.name in {"write", "edit", "bash", "write_process_input"}:
                        result = await self._execute_project_operation(definition, parsed, context, fingerprint)
                    else:
                        result = await definition.execute(parsed, context, self)
                        if context.project_workspace_id and definition.name == "stop_process":
                            from agent.modules.projects import get_project_workspace_service
                            projects = get_project_workspace_service()
                            async with projects.lock(context.project_workspace_id):
                                result = await projects.persist_result(context, result)
                    result = definition.output_schema.model_validate(result)
                    if definition.render:
                        result.content = definition.render(result)
                except (CodingError, OSError, ValueError) as exc:
                    result = self.failure(exc)
                except Exception as exc:
                    from langgraph.errors import GraphInterrupt
                    if isinstance(exc, GraphInterrupt):
                        raise
                    logger.error("Unexpected coding tool failure", extra={"tool_name": definition.name,
                                 "exception_type": type(exc).__name__})
                    result = ToolResult(status="error", error=ResultError(code="unexpected", message="Tool execution failed unexpectedly."),
                                        content="[error] unexpected: Tool execution failed unexpectedly.")
                result = self.model_result(definition.name, result, context)
                result = self.storage.bound(result, context, tail=definition.name in PROCESS_TOOLS)
                if use_journal:
                    self.storage.complete(context, fingerprint, result)
                if not context.project_workspace_id and context.backend != "local" and context.thread_id and definition.name in {
                    "write", "edit", "bash", "write_process_input", "stop_process",
                }:
                    # Settle effects before checkpointing so a failed save cannot replay the command.
                    await self._persist_thread_result(context, result)
                    if use_journal:
                        self.storage.complete(context, fingerprint, result)
                return result
        except (CodingError, ValidationError, OSError, ValueError) as exc:
            result = self.failure(exc)
            result = self.storage.bound(result, context, tail=definition.name in PROCESS_TOOLS)
            return result
        finally:
            elapsed = time.monotonic() - started
            status = result.status if result else "interrupted"
            received = result.data.get("received_bytes", 0) if result else 0
            rendered_bytes = len(str(result.content).encode("utf-8")) if result else 0
            capture_cut = result.capture_truncated if result else False
            display_cut = result.output_truncated if result else False
            logger.info("Coding tool=%s call=%s status=%s duration_seconds=%.3f received_bytes=%s output_bytes=%d capture_truncated=%s output_truncated=%s",
                        definition.name, context.tool_call_id, status, elapsed, received, rendered_bytes, capture_cut, display_cut,
                        extra={"tool_name": definition.name, "arguments_fingerprint": fingerprint,
                        "tool_call_id": context.tool_call_id, "duration_seconds": elapsed,
                        "journal_started": journal_started, "status": result.status if result else "interrupted",
                        "received_bytes": received, "output_bytes": rendered_bytes,
                        "capture_truncated": capture_cut, "output_truncated": display_cut})
            # Do not grow the lock catalog once an invocation has settled.
            if not lock.locked() and not getattr(lock, "_waiters", None):
                self.invocation_locks.pop(key, None)

    def model_result(self, name: str, result: ToolResult, context: InvocationContext) -> ToolResult:
        content = result.model_content(name)
        if content != result.content:
            if result.display_content is None and isinstance(result.content, str):
                result.display_content = result.content
                result.display_truncated |= result.output_truncated
            result.content = content
            result.output_truncated = False
            result = self.storage.bound(result, context)
        return result


    @staticmethod
    def failure(exc: Exception) -> ToolResult:
        code = getattr(exc, "code", "not_found" if isinstance(exc, FileNotFoundError) else "invalid_input" if isinstance(exc, (ValueError, ValidationError)) else "execution_error")
        error = ResultError(code=code, message=str(exc), details=getattr(exc, "details", {}))
        return ToolResult(status="error", error=error, content=f"[error] {code}: {exc}")


def get_coding_service(container: Any = None) -> CodingService:
    from agent.bootstrap.container import require_active_container
    active = require_active_container(container)
    with active._lazy_lock:
        if active._coding_service is None:
            config = active.config_service
            configured = config.get("tools.storage_root", None)
            base = Path(configured).expanduser() if configured else Path.home() / ".k41-agent" / "coding-v2"
            root = base / digest(active.database_url)[:32]
            active._coding_service = CodingService(root, shell=config.get("tools.shell", None))
        return active._coding_service
