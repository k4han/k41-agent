"""Shared operation engine executed on the host or inside a sandbox."""

from __future__ import annotations

import asyncio
from typing import Any

from agent.modules.tools.coding.contracts import CodingError, InvocationContext, RuntimeResult as ToolResult
from agent.modules.tools.coding.names import FILE_TOOLS
from agent.modules.tools.coding.paths import comparable_path
from agent.modules.tools.coding.storage import digest
from agent.modules.tools.runtime.shell_guard import check_command_blocked


class CodingEngine:
    async def prepare(self, name: str, values: dict[str, Any], context: InvocationContext) -> None:
        if name in FILE_TOOLS:
            self.files.prepare(name, values, context)
            if name in {"grep", "glob"}:
                base = self.permissions.resolve_path(context, values.get("path", ""), "read", authorize=False)
                self.permissions.assert_allowed(context, name, values["pattern"], root=str(base), include=values.get("include"))
                candidates, _ = await asyncio.to_thread(self.files.search_candidates, base, values.get("include_dirs", False))
                for candidate in candidates:
                    resolved = candidate.resolve()
                    if resolved.is_relative_to(self.storage.root.resolve()):
                        continue
                    if resolved.is_relative_to(base if base.is_dir() else base.parent):
                        self.permissions.assert_allowed(context, "read", str(resolved))
        elif name == "bash":
            command = values["command"]
            blocked, reason = check_command_blocked(command)
            if blocked:
                raise CodingError("permission_denied", f"Blocked dangerous command: {reason}")
            cwd = self.permissions.resolve_path(context, values.get("workdir") or ".", "shell", authorize=False)
            if not comparable_path(cwd).is_relative_to(comparable_path(context.workspace)) and not self.permissions.is_skill_path(context, cwd):
                self.permissions.assert_allowed(context, "external_directory", str(cwd))
            self.permissions.assert_allowed(context, "shell", command, shell=self.shell, workdir=str(cwd))
            if not cwd.is_dir():
                raise CodingError("invalid_input", f"Working directory is not a directory: {cwd}")
        else:
            self.processes.get(context, values["process_id"])
            metadata = {"input_hash": digest(values["text"])} if name == "write_process_input" else {}
            self.permissions.assert_allowed(context, name, values["process_id"], **metadata)

    async def execute(self, name: str, values: dict[str, Any], context: InvocationContext) -> ToolResult:
        if name in FILE_TOOLS:
            return await self.files.execute(name, values, context)
        if name == "bash":
            cwd = self.permissions.resolve_path(context, values.get("workdir") or ".", "shell", authorize=False)
            if not comparable_path(cwd).is_relative_to(comparable_path(context.workspace)) and not self.permissions.is_skill_path(context, cwd):
                self.permissions.assert_allowed(context, "external_directory", str(cwd), allow_interrupt=False)
            self.permissions.assert_allowed(context, "shell", values["command"], shell=self.shell,
                                            workdir=str(cwd), allow_interrupt=False)
            job = await self.processes.start(context, values["command"], cwd, values["timeout_seconds"], self.shell)
            try:
                return await self.processes.observe(job, yield_time_ms=values["yield_time_ms"], preview=True)
            except asyncio.CancelledError:
                await self.processes.stop(job)
                raise
        job = self.processes.get(context, values["process_id"])
        if name == "write_process_input":
            await self.processes.send(job, values["text"])
        elif name == "stop_process":
            await self.processes.stop(job)
        return await self.processes.observe(job, cursor=values.get("cursor", 0), yield_time_ms=values.get("yield_time_ms", 0))

