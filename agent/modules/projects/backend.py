"""Project-aware facade around provider file and command capabilities."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import posixpath
from dataclasses import replace

from agent.modules.projects.workspaces import ProjectWorkspaceError, get_project_workspace_service
from agent.modules.workspaces import CommandResult

MUTATIONS = frozenset({"write_text", "write_bytes", "rename", "delete", "create_file", "create_directory", "execute", "clone_repository"})
READS = frozenset({"read_text", "read_bytes", "list_dir", "glob", "grep", "tree", "tree_page", "file", "changes", "diff"})


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
        value = posixpath.normpath(str(path).replace("\\", "/") or ".")
        if value.startswith("/"):
            root = posixpath.normpath(self.root)
            if value == root:
                return ""
            if not value.startswith(root.rstrip("/") + "/"):
                raise ValueError("Path escapes workspace.")
            value = posixpath.relpath(value, root)
        if value == ".." or value.startswith("../"):
            raise ValueError("Path escapes workspace.")
        return "" if value == "." else value

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
                from agent.modules.workspaces.host_mirror import workspace_is_running
                try:
                    _, _, runtime = await self.service.rows(self.binding.project_id, self.binding.project_workspace_id)
                    if runtime.generation != self.binding.runtime_generation:
                        raise ProjectWorkspaceError("Execution runtime changed.", "stale_runtime")
                    if runtime.status in {"ready", "stopped"} and await workspace_is_running(self.ref):
                        ref = self.ref.model_copy(deep=True)
                        ref.metadata["_require_running"] = True
                        backend = await create_workspace_runtime_backend(ref)
                        result = await getattr(backend, name)(*args, **kwargs)
                        if isinstance(result, dict):
                            result.update(data_source="live", stale=False)
                        return result
                except ProjectWorkspaceError:
                    raise
                except Exception:
                    # Saved browsing remains available during provider/network outages.
                    pass
                args = list(args)
                if args and name in {"read_text", "read_bytes", "list_dir", "tree", "tree_page", "file", "diff"}:
                    args[0] = self._relative_path(args[0])
                for key in {"path", "file_path"} & kwargs.keys():
                    kwargs[key] = self._relative_path(kwargs[key])
                async with self.service.saved_view(self.binding.project_id, self.binding.project_workspace_id) as view:
                    backend = await create_workspace_runtime_backend(view)
                    if name in {"changes", "diff"} and not (Path(view.locator) / ".git").exists():
                        result = {"root": view.locator, "is_git_repo": False, "message": "Workspace is not a Git repository."}
                        if name == "changes":
                            result["changes"] = []
                        else:
                            result.update(path=str(Path(view.locator) / (args[0] if args else kwargs["path"])),
                                          status="", diff="", truncated=False)
                    else:
                        result = await getattr(backend, name)(*args, **kwargs)
                    result = self._present_paths(result, view.locator)
                    if isinstance(result, dict):
                        result.update(saved_revision=view.metadata["saved_revision"],
                                      last_saved_at=view.metadata["last_saved_at"], data_source="saved", stale=True)
                    return result
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

    def __getattr__(self, name):
        async def operation(*args, **kwargs):
            from agent.modules.workspaces import create_workspace_runtime_backend
            from agent.modules.workspaces.host_mirror import workspace_is_running
            if name not in READS:
                from agent.modules.workspaces.host_mirror import thread_storage_key
                rows, _ = await self.service.host_mirrors.thread_status(self.thread_id)
                key = thread_storage_key(rows)
                with self.service.switcher.run_lease(-int(key)):
                    async with self.service.host_mirrors.thread_locks.setdefault(key, asyncio.Lock()):
                        await self.service.host_mirrors._refresh_threads(rows, self.ref)
                        # Recovery must restore the durable mirror before rebinding this workspace.
                        backend = await create_workspace_runtime_backend(self.ref)
                        return await getattr(backend, name)(*args, **kwargs)
            try:
                if await workspace_is_running(self.ref):
                    ref = self.ref.model_copy(deep=True)
                    ref.metadata["_require_running"] = True
                    backend = await create_workspace_runtime_backend(ref)
                    result = await getattr(backend, name)(*args, **kwargs)
                    if isinstance(result, dict):
                        result.update(data_source="live", stale=False)
                    return result
            except Exception:
                pass
            async with self.service.host_mirrors.saved_thread_view(self.thread_id) as view:
                backend = await create_workspace_runtime_backend(view)
                args = list(args)
                if args and name in {"read_text", "read_bytes", "list_dir", "tree", "tree_page", "file", "diff"}:
                    args[0] = self._relative_path(args[0])
                for key in {"path", "file_path"} & kwargs.keys():
                    kwargs[key] = self._relative_path(kwargs[key])
                result = self._present_paths(await getattr(backend, name)(*args, **kwargs), view.locator)
                if isinstance(result, dict):
                    result.update(data_source="saved", stale=True, saved_revision=view.metadata["saved_revision"],
                                  last_saved_at=view.metadata["last_saved_at"])
                return result
        return operation
