"""Shared provider transition steps with caller-owned durable state."""


class RuntimeTransitionCoordinator:
    @staticmethod
    def allocation_uncertain(error):
        if isinstance(error, (TimeoutError, ConnectionError)) or getattr(error, "code", None) == "unknown_outcome":
            return True
        status = getattr(error, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return True
        try:
            import httpx
            if isinstance(error, httpx.TransportError):
                return True
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code >= 500:
                return True
        except ImportError:
            pass
        try:
            from daytona.common.errors import DaytonaConnectionError, DaytonaTimeoutError
            if isinstance(error, (DaytonaConnectionError, DaytonaTimeoutError)):
                return True
        except ImportError:
            pass
        try:
            from modal.exception import InternalError, ServiceError
            if isinstance(error, (InternalError, ServiceError)):
                return True
        except ImportError:
            pass
        return False

    @staticmethod
    def operation_id(label):
        value = str(label or "").removeprefix("k41-switch-")
        return value if str(label or "").startswith("k41-switch-") and len(value) == 32 and all(c in "0123456789abcdef" for c in value) else None

    @staticmethod
    async def find_allocation(backend, operation_id):
        if not operation_id:
            return None
        from agent.modules.workspaces import call_workspace_backend_loader, get_workspace_backend_registry, WorkspaceRef
        descriptor = get_workspace_backend_registry().require(backend)
        if not descriptor.inventory_loader:
            return None
        records = await call_workspace_backend_loader(backend, descriptor.inventory_loader, in_thread=backend == "daytona")
        matches = [record for record in (records or [])
                   if (record.get("metadata") or {}).get("k41_operation_id") == operation_id]
        if len(matches) != 1:
            return None
        record = matches[0]
        return WorkspaceRef(backend=backend, locator=record["sandbox_id"],
                            metadata={"root": record.get("root") or "/workspace"})

    @staticmethod
    async def prepare_cloud(backend, transfer, store, key, revision, *, on_created, phase, label=None):
        from agent.modules.workspaces import create_workspace_backend
        await phase("provisioning")
        recorded = False
        async def remember(ref):
            nonlocal recorded
            await on_created(ref)
            recorded = True
        try:
            target = await create_workspace_backend(backend, label=label, on_created=remember)
        except Exception as exc:
            if RuntimeTransitionCoordinator.allocation_uncertain(exc):
                raise ConnectionError("Allocation outcome is unknown; the source runtime has been retained.") from exc
            raise
        if not recorded:
            await remember(target)
        await phase("restoring")
        await transfer.restore(target, store, key, revision)
        await phase("verifying")
        await transfer.verify(target, store, key, revision)
        target.metadata.update(files_restored=True, environment_state="unknown")
        return target
