from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from agent.shared.config.service import ConfigService, get_config_service
from agent.shared.integrations import (
    IntegrationDescriptor,
    LazyIntegrationRegistry,
)

LOCAL_BACKEND = "local"
DAYTONA_BACKEND = "daytona"
MODAL_BACKEND = "modal"


@dataclass(frozen=True, slots=True)
class WorkspaceBackendDescriptor(IntegrationDescriptor):
    supports_sandbox_inventory: bool = False
    supports_lifecycle: bool = False
    supports_repository_clone: bool = False
    backend_factory_loader: str = ""
    create_loader: str = ""
    attach_loader: str = ""
    delete_loader: str = ""
    stop_loader: str = ""
    archive_loader: str = ""
    inventory_loader: str = ""
    sweeper_start_loader: str = ""
    sweeper_stop_loader: str = ""
    sweeper_run_loader: str = ""
    requires_api_key: bool = False


BUILTIN_WORKSPACE_BACKEND_DESCRIPTORS = (
    WorkspaceBackendDescriptor(
        kind="workspace_backend",
        name=LOCAL_BACKEND,
        title="Local",
        summary="Use a local filesystem directory as the workspace.",
        config_prefix="workspace",
        loader="agent.modules.workspaces.local_backend:LocalWorkspaceBackend",
        capabilities=frozenset({"file_io", "commands", "browser", "changes"}),
        backend_factory_loader=(
            "agent.modules.workspaces.local_backend:create_local_backend"
        ),
    ),
    WorkspaceBackendDescriptor(
        kind="workspace_backend",
        name=DAYTONA_BACKEND,
        title="Daytona",
        summary="Run workspaces in Daytona sandboxes.",
        config_prefix="workspace.daytona",
        loader="agent.modules.workspaces.daytona_backend:DaytonaWorkspaceBackend",
        capabilities=frozenset(
            {
                "file_io",
                "commands",
                "browser",
                "changes",
                "repository_clone",
                "lifecycle",
                "sandbox_inventory",
                "sweeper",
            }
        ),
        dependency_imports=("daytona",),
        install_extra="sandbox-daytona",
        supports_sandbox_inventory=True,
        supports_lifecycle=True,
        supports_repository_clone=True,
        requires_api_key=True,
        backend_factory_loader=(
            "agent.modules.workspaces.daytona_backend:create_daytona_backend"
        ),
        create_loader="agent.modules.workspaces.daytona_backend:create_daytona_workspace",
        attach_loader="agent.modules.workspaces.daytona_backend:attach_daytona_workspace",
        delete_loader="agent.modules.workspaces.daytona_backend:delete_daytona_workspace",
        stop_loader="agent.modules.workspaces.daytona_backend:stop_daytona_workspace",
        archive_loader="agent.modules.workspaces.daytona_backend:archive_daytona_workspace",
        inventory_loader="agent.modules.workspaces.daytona_backend:list_daytona_cloud_sandboxes",
        sweeper_start_loader=(
            "agent.modules.workspaces.daytona_backend:start_daytona_lifecycle_sweeper"
        ),
        sweeper_stop_loader=(
            "agent.modules.workspaces.daytona_backend:stop_daytona_lifecycle_sweeper"
        ),
        sweeper_run_loader="agent.modules.workspaces.daytona_backend:sweep_idle_daytona_workspaces",
    ),
    WorkspaceBackendDescriptor(
        kind="workspace_backend",
        name=MODAL_BACKEND,
        title="Modal",
        summary="Run workspaces in Modal sandboxes.",
        config_prefix="workspace.modal",
        loader="agent.modules.workspaces.modal_backend:ModalWorkspaceBackend",
        capabilities=frozenset(
            {
                "file_io",
                "commands",
                "browser",
                "changes",
                "repository_clone",
                "lifecycle",
                "sandbox_inventory",
            }
        ),
        dependency_imports=("modal",),
        install_extra="sandbox-modal",
        supports_sandbox_inventory=True,
        supports_lifecycle=True,
        supports_repository_clone=True,
        backend_factory_loader=(
            "agent.modules.workspaces.modal_backend:create_modal_backend"
        ),
        create_loader="agent.modules.workspaces.modal_backend:create_modal_workspace",
        attach_loader="agent.modules.workspaces.modal_backend:attach_modal_workspace",
        delete_loader="agent.modules.workspaces.modal_backend:delete_modal_workspace",
        inventory_loader="agent.modules.workspaces.modal_backend:list_modal_cloud_sandboxes",
    ),
)


class WorkspaceBackendRegistry:
    def __init__(self) -> None:
        self._lazy = LazyIntegrationRegistry("workspace_backend")

    def register(
        self,
        descriptor: WorkspaceBackendDescriptor,
        *,
        replace: bool = False,
    ) -> None:
        self._lazy.register(descriptor, replace=replace)

    def descriptor(self, name: str) -> WorkspaceBackendDescriptor | None:
        descriptor = self._lazy.get_descriptor(name)
        return descriptor if isinstance(descriptor, WorkspaceBackendDescriptor) else None

    def require(self, name: str) -> WorkspaceBackendDescriptor:
        descriptor = self.descriptor(name)
        if descriptor is None:
            raise KeyError(
                f"Workspace backend '{name}' is not registered. "
                f"Available: {self.names()}"
            )
        return descriptor

    def names(self) -> list[str]:
        return self._lazy.names()

    def list(self) -> list[WorkspaceBackendDescriptor]:
        return [
            descriptor
            for descriptor in self._lazy.list_descriptors()
            if isinstance(descriptor, WorkspaceBackendDescriptor)
        ]

    def availability(self, name: str):
        return self._lazy.availability(name)

    def load_backend_type(self, name: str) -> Any:
        return self._lazy.resolve(name)

    def resolve_loader(self, name: str, loader: str) -> Any:
        return self._lazy.resolve_loader(name, loader)


_registry = WorkspaceBackendRegistry()
_builtins_registered = False


def get_workspace_backend_registry() -> WorkspaceBackendRegistry:
    ensure_builtin_workspace_backend_descriptors()
    return _registry


def ensure_builtin_workspace_backend_descriptors() -> None:
    global _builtins_registered
    if _builtins_registered:
        return
    for descriptor in BUILTIN_WORKSPACE_BACKEND_DESCRIPTORS:
        _registry.register(descriptor, replace=True)
    _builtins_registered = True


def _is_backend_enabled(
    descriptor: WorkspaceBackendDescriptor,
    *,
    config_service: ConfigService | None = None,
) -> bool:
    if descriptor.name == LOCAL_BACKEND:
        return True
    if not descriptor.config_prefix:
        return True
    service = config_service or get_config_service()
    return service.get_bool(f"{descriptor.config_prefix}.enabled", False)


def list_workspace_backend_catalog(
    *,
    config_service: ConfigService | None = None,
) -> list[dict[str, Any]]:
    registry = get_workspace_backend_registry()
    catalog: list[dict[str, Any]] = []
    for descriptor in registry.list():
        availability = registry.availability(descriptor.name)
        catalog.append(
            {
                "name": descriptor.name,
                "title": descriptor.title,
                "summary": descriptor.summary,
                "capabilities": sorted(descriptor.capabilities),
                "availability": availability.to_dict(),
                "install_extra": descriptor.install_extra,
                "enabled": _is_backend_enabled(descriptor, config_service=config_service),
            }
        )
    return catalog


def is_registered_workspace_backend(name: str) -> bool:
    return get_workspace_backend_registry().descriptor(name) is not None


async def call_workspace_backend_loader(
    backend: str,
    loader: str,
    *args: Any,
    in_thread: bool = False,
    **kwargs: Any,
) -> Any:
    """Resolve a registered loader and invoke it with sync/async tolerance.

    ``in_thread=True`` runs the callable in a worker thread so blocking SDK
    calls (Daytona) do not stall the event loop; the result is awaited if it
    happens to be a coroutine.
    """
    resolved = get_workspace_backend_registry().resolve_loader(backend, loader)
    if in_thread:
        result = await asyncio.to_thread(resolved, *args, **kwargs)
    else:
        result = resolved(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = [
    "BUILTIN_WORKSPACE_BACKEND_DESCRIPTORS",
    "DAYTONA_BACKEND",
    "LOCAL_BACKEND",
    "MODAL_BACKEND",
    "WorkspaceBackendDescriptor",
    "WorkspaceBackendRegistry",
    "call_workspace_backend_loader",
    "ensure_builtin_workspace_backend_descriptors",
    "get_workspace_backend_registry",
    "is_registered_workspace_backend",
    "list_workspace_backend_catalog",
]
