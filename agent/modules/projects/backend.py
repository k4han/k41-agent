"""Project-aware facade around provider file and command capabilities."""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from pathlib import Path
import posixpath
from dataclasses import replace

from agent.modules.projects.workspaces import ProjectWorkspaceError, get_project_workspace_service
from agent.modules.workspaces import CommandResult
from agent.modules.workspaces import relative_remote_path, resolve_remote_path
from agent.modules.workspaces import READS, STRUCTURED_READS, WorkspaceReadService
from agent.modules.workspaces import WorkspaceReadResult

MUTATIONS = frozenset({"write_text", "write_bytes", "rename", "delete", "create_file", "create_directory", "execute", "clone_repository"})
logger = logging.getLogger(__name__)


class ManagedWorkspaceBackend:
    def __init__(self, backend, binding, service):
        self.backend, self.binding, self.service = backend, binding, service

    @property
    def ref(self):
        return self.backend.ref

    async def read_result(self, name, *args, **kwargs):
        if name not in READS:
            raise ValueError("Unsupported workspace read operation.")
        _, _, runtime = await self.service.rows(self.binding.project_id, self.binding.project_workspace_id)
        if runtime.generation != self.binding.runtime_generation:
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        kwargs.pop("consistency", None)
        kwargs.pop("recover", None)
        result = await getattr(self.backend, name)(*args, **kwargs)
        _, _, runtime = await self.service.rows(self.binding.project_id, self.binding.project_workspace_id)
        if runtime.generation != self.binding.runtime_generation:
            raise ProjectWorkspaceError("Execution runtime changed during the read.", "stale_runtime")
        return WorkspaceReadResult(result, "live", self.binding.runtime_generation)

    def __getattr__(self, name):
        member = getattr(self.backend, name)
        if name not in MUTATIONS:
            return member

        async def operation(*args, **kwargs):
            async with self.service.mutation(self.binding.project_id, self.binding.project_workspace_id,
                                             generation=self.binding.runtime_generation):
                try:
                    result = member(*args, **kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                except BaseException:
                    try:
                        await self.service._checkpoint(self.binding.project_id, self.binding.project_workspace_id,
                                                       self.binding.runtime_generation)
                    except Exception:
                        pass
                    raise
                try:
                    saved = await self.service._checkpoint(self.binding.project_id, self.binding.project_workspace_id,
                                                           self.binding.runtime_generation)
                except ProjectWorkspaceError as exc:
                    saved = {"status": "error", "saved": False, "message": str(exc)}
                if isinstance(result, dict):
                    result["persistence"] = saved
                elif isinstance(result, CommandResult):
                    output = result.output
                    if not saved["saved"]:
                        output += "\n[warning] Project changes have not been saved. Retry checkpoint before writing."
                    result = replace(result, output=output, persistence=saved)
                elif isinstance(result, str) and not saved["saved"]:
                    result += "\n[warning] Project changes have not been saved. Retry checkpoint before writing."
                return result
        return operation


class DeferredWorkspaceBackend:
    """Read durable cloud files without allocating an execution environment."""

    def __init__(self, binding, service):
        self.binding, self.service = binding, service

    @property
    def ref(self):
        return self.binding.execution

    @property
    def root(self):
        return str(self.ref.metadata.get("root") or "")

    def _relative_path(self, path):
        if path is None:
            return None
        return relative_remote_path(self.root or "/", resolve_remote_path(self.root or "/", path))

    async def _read_generation(self):
        _, _, runtime = await self.service.rows(self.binding.project_id, self.binding.project_workspace_id)
        if runtime.generation != self.binding.runtime_generation:
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        self._read_status = runtime.status
        return runtime.generation

    def _saved_view(self):
        return self.service.saved_view(self.binding.project_id, self.binding.project_workspace_id)

    async def _recover_read(self):
        self.binding = await self.service.ensure(self.binding.project_id, self.binding.project_workspace_id, recover=True)

    async def read_result(self, name, *args, **kwargs):
        return await WorkspaceReadService().read(self, name, *args, **kwargs)

    def _present_paths(self, result, local_root):
        if isinstance(result, list):
            return [self._present_paths(item, local_root) for item in result]
        if not isinstance(result, dict):
            return result
        presented = {}
        for key, value in result.items():
            if key in {"root", "path", "old_path", "new_path"} and isinstance(value, str):
                try:
                    relative = Path(value).relative_to(local_root).as_posix()
                    value = self.root if relative == "." else posixpath.join(self.root, relative)
                except ValueError:
                    pass
            presented[key] = self._present_paths(value, local_root)
        return presented

    def __getattr__(self, name):
        async def operation(*args, **kwargs):
            from agent.modules.workspaces import create_workspace_runtime_backend
            if name in READS:
                consistency = kwargs.pop("consistency", "saved_ok" if name in STRUCTURED_READS else "live_required")
                return (await self.read_result(name, *args, consistency=consistency, **kwargs)).legacy()
            self.binding = await self.service.ensure(self.binding.project_id, self.binding.project_workspace_id, recover=True)
            backend = await create_workspace_runtime_backend(self.ref)
            managed = ManagedWorkspaceBackend(backend, self.binding, self.service)
            result = getattr(managed, name)(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        return operation


async def managed_backend(workspace, *, thread_id=None):
    from agent.modules.workspaces import create_workspace_runtime_backend as _create_workspace_runtime_backend
    from agent.modules.workspaces import resolve_workspace_ref
    service = get_project_workspace_service()
    binding = await service.find_binding(workspace, thread_id=thread_id)
    if binding is None:
        ref = resolve_workspace_ref(workspace)
        if thread_id and service.container._persistence_ready and ref.backend != "local":
            return ThreadWorkspaceBackend(ref, thread_id, service)
        return await _create_workspace_runtime_backend(ref, thread_id=thread_id)
    project, _, _ = await service.rows(binding.project_id, binding.project_workspace_id)
    if project.migration_status == "ready" and binding.execution.backend != "local":
        return DeferredWorkspaceBackend(binding, service)
    if project.migration_status == "ready":
        binding = await service.ensure(binding.project_id, binding.project_workspace_id, recover=True)
    backend = await _create_workspace_runtime_backend(binding.execution)
    return ManagedWorkspaceBackend(backend, binding, service)


class ThreadWorkspaceBackend(DeferredWorkspaceBackend):
    """Browse temporary cloud workspaces without implicitly waking their provider."""

    def __init__(self, ref, thread_id, service):
        self.execution = ref
        self.thread_id = thread_id
        self.service = service

    @property
    def ref(self):
        return self.execution

    async def _read_generation(self):
        from agent.modules.projects.workspaces import source_key
        from agent.modules.workspaces import mirror_state, transition_active
        try:
            rows, _ = await self.service.host_mirrors.thread_status(self.thread_id)
        except KeyError:
            if "_generation" in self.__dict__:
                raise ProjectWorkspaceError("Thread workspace was detached.", "stale_runtime")
            self._read_status = "ready"
            return None
        if source_key(self.service.host_mirrors.thread_ref(rows[0])) != source_key(self.ref):
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        state = mirror_state(rows[0])
        generation = state.get("generation") or source_key(self.ref)
        if "_generation" in self.__dict__ and self._generation != generation:
            raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
        self._generation = generation
        self._read_status = "restoring" if transition_active(state.get("transition")) else "ready"
        return generation

    @asynccontextmanager
    async def _saved_view(self):
        from agent.modules.workspaces import WorkspaceUnavailableError
        try:
            async with self.service.host_mirrors.saved_thread_view(self.thread_id) as view:
                yield view
        except KeyError as exc:
            raise WorkspaceUnavailableError("No saved thread files are available.") from exc

    async def _recover_read(self):
        from agent.modules.workspaces import attach_workspace_backend
        self.execution = await attach_workspace_backend(self.ref.backend, self.ref.locator,
                                                       label=self.ref.label, root=self.root)

    def __getattr__(self, name):
        async def operation(*args, **kwargs):
            from agent.modules.workspaces import create_workspace_runtime_backend
            if name not in READS:
                from agent.modules.workspaces import thread_storage_key
                rows, _ = await self.service.host_mirrors.thread_status(self.thread_id)
                key = thread_storage_key(rows)
                with self.service.switcher.run_lease(-int(key)):
                    try:
                        async with self.service.host_mirrors.thread_locks.setdefault(key, asyncio.Lock()):
                            rows = await self.service.host_mirrors._refresh_threads(rows, self.ref)
                            from agent.modules.workspaces import mirror_state, transition_active
                            if transition_active(mirror_state(rows[0]).get("transition")):
                                raise ProjectWorkspaceError("The runtime transition must be reconciled or abandoned.", "workspace_switching")
                            backend = await create_workspace_runtime_backend(self.ref)
                            return await getattr(backend, name)(*args, **kwargs)
                    finally:
                        try:
                            await self.service.host_mirrors._poll_threads(rows, force=True)
                        except Exception:
                            logger.warning("Thread operation completed with files pending checkpoint thread=%s", self.thread_id, exc_info=True)
            consistency = kwargs.pop("consistency", "saved_ok" if name in STRUCTURED_READS else "live_required")
            return (await self.read_result(name, *args, consistency=consistency, **kwargs)).legacy()
        return operation
