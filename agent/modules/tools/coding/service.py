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
from agent.modules.tools.coding.names import CODING_TOOLS, FILE_TOOLS, PROCESS_TOOLS
from agent.modules.tools.coding.models import CodingError, InvocationContext, ResultError, ToolDefinition, ToolResult
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
        self.storage.cleanup()

    async def prepare(self, name: str, values: dict[str, Any], context: InvocationContext) -> None:
        if self._uses_remote(name, values, context):
            await self.remote.prepare(name, values, context)
        else:
            await super().prepare(name, values, context)

    async def execute(self, name: str, values: dict[str, Any], context: InvocationContext) -> ToolResult:
        if self._uses_remote(name, values, context):
            return await self.remote.execute(name, values, context)
        result = await super().execute(name, values, context)
        return ToolResult.model_validate(result.to_dict())

    def _uses_remote(self, name: str, values: dict[str, Any], context: InvocationContext) -> bool:
        if context.backend == "local":
            return False
        if name == "read_tool_output":
            try:
                self.storage.output_path(context, values["output_ref"])
                return False
            except CodingError as exc:
                if exc.code != "not_found":
                    raise
        return True

    async def invoke(self, definition: ToolDefinition, args: dict[str, Any], context: InvocationContext) -> ToolResult:
        started = time.monotonic()
        fingerprint = digest(json.dumps([definition.name, args], sort_keys=True, ensure_ascii=True))
        key = f"{context.owner}\0{context.message_id}\0{context.tool_call_id}" if context.tool_call_id else os.urandom(16).hex()
        lock = self.invocation_locks.setdefault(key, asyncio.Lock())
        journal_started = False
        result = None
        try:
            parsed = definition.input_schema.model_validate(args)
            values = parsed.model_dump()
            async with lock:
                # Completed settlements are returned before authorization so
                # replay cannot consume a resume value intended for another call.
                if context.tool_call_id:
                    path = self.storage.journal_path(context)
                    if path.exists():
                        result = self.storage.begin(context, fingerprint)
                        result = self.model_result(definition.name, result, context)
                        return result
                await self.prepare(definition.name, values, context)
                cached = self.storage.begin(context, fingerprint)
                if cached is not None:
                    result = self.model_result(definition.name, cached, context)
                    return result
                journal_started = True
                try:
                    result = await definition.execute(parsed, context, self)
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
