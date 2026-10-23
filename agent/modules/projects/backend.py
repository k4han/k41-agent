"""Project-aware facade around provider file and command capabilities."""

from __future__ import annotations

import inspect
from dataclasses import replace

from agent.modules.projects.workspaces import ProjectWorkspaceError, get_project_workspace_service
from agent.modules.workspaces import CommandResult

MUTATIONS = frozenset({"write_text", "write_bytes", "rename", "delete", "create_file", "create_directory", "execute", "clone_repository"})


class ManagedWorkspaceBackend:
    def __init__(self, backend, binding, service):
        self.backend, self.binding, self.service = backend, binding, service

    @property
    def ref(self):
        return self.backend.ref

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


async def managed_backend(workspace, *, thread_id=None):
    from agent.modules.workspaces import create_workspace_runtime_backend as _create_workspace_runtime_backend
    service = get_project_workspace_service()
    binding = await service.find_binding(workspace, thread_id=thread_id)
    if binding is None:
        return await _create_workspace_runtime_backend(workspace, thread_id=thread_id)
    project, _, _ = await service.rows(binding.project_id, binding.project_workspace_id)
    if project.migration_status == "ready":
        binding = await service.ensure(binding.project_id, binding.project_workspace_id)
    backend = await _create_workspace_runtime_backend(binding.execution)
    return ManagedWorkspaceBackend(backend, binding, service)
