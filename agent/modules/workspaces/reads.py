"""Source-aware reads shared by durable project and temporary thread facades."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Literal, TypeVar

import anyio

from agent.modules.workspaces.backends import WorkspaceUnavailableError

T = TypeVar("T")
ReadConsistency = Literal["live_required", "saved_ok"]
READS = frozenset({"read_text", "read_bytes", "stream_bytes", "list_dir", "glob", "grep", "tree", "tree_page", "file", "changes", "diff"})
STRUCTURED_READS = frozenset({"tree", "tree_page", "file", "changes", "diff"})
PATH_FIRST = frozenset({"read_text", "read_bytes", "stream_bytes", "list_dir", "tree", "tree_page", "file", "diff"})


class WorkspaceByteStream:
    """A prefetched byte stream that owns its reader and saved-view lifetime."""

    def __init__(self, iterator, first, stack, check):
        self._iterator = iterator
        self._first = first
        self._stack = stack
        self._check = check
        self._closed = False

    @classmethod
    async def open(
        cls, iterator: AsyncIterator[bytes], *, stack: AsyncExitStack | None = None,
        check: Callable[[], Awaitable[object]] | None = None,
    ) -> WorkspaceByteStream:
        stack = stack if stack is not None else AsyncExitStack()
        await stack.enter_async_context(aclosing(iterator))
        try:
            first = await anext(iterator, None)
        except BaseException as exc:
            with anyio.CancelScope(shield=True):
                await stack.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        return cls(iterator, first, stack, check)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._closed:
            raise StopAsyncIteration
        try:
            if self._first is not None:
                first, self._first = self._first, None
                return first
            try:
                return await anext(self._iterator)
            except StopAsyncIteration:
                # Fence the completed read without querying storage for every chunk.
                if self._check is not None:
                    await self._check()
                raise
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._first = None
            with anyio.CancelScope(shield=True):
                await self._stack.aclose()


async def provider_read_error(ref, error):
    """Translate SDK errors without confusing missing files with lost runtimes."""
    if isinstance(error, (FileNotFoundError, PermissionError, ValueError, WorkspaceUnavailableError, TimeoutError, ConnectionError)):
        return error
    try:
        import httpx
        if isinstance(error, httpx.TransportError):
            return ConnectionError("The workspace provider is temporarily unavailable.")
    except ImportError:
        pass
    try:
        if ref.backend == "daytona":
            from daytona.common.errors import (
                DaytonaAuthenticationError, DaytonaAuthorizationError, DaytonaConnectionError,
                DaytonaNotFoundError, DaytonaTimeoutError, DaytonaValidationError,
            )
            denied = isinstance(error, (DaytonaAuthenticationError, DaytonaAuthorizationError))
            unavailable = isinstance(error, (DaytonaConnectionError, DaytonaTimeoutError))
            missing = isinstance(error, DaytonaNotFoundError)
            invalid = isinstance(error, DaytonaValidationError)
        elif ref.backend == "modal":
            from modal.exception import AuthError, InvalidError, NotFoundError, PermissionDeniedError, ServiceError
            denied = isinstance(error, (AuthError, PermissionDeniedError))
            unavailable = isinstance(error, ServiceError)
            missing = isinstance(error, NotFoundError)
            invalid = isinstance(error, InvalidError)
        elif ref.backend == "instabox":
            from agent.modules.instabox import InstaboxApiError
            if not isinstance(error, InstaboxApiError):
                return error
            denied = error.is_auth_error
            unavailable = error.retryable or error.status >= 500 or error.status == 429
            missing = error.is_not_found
            invalid = error.status in {400, 422}
        else:
            return error
    except ImportError:
        return error
    if denied:
        return PermissionError("The workspace provider denied access.")
    if unavailable:
        return ConnectionError("The workspace provider is temporarily unavailable.")
    if missing:
        from agent.modules.workspaces.host_mirror import workspace_runtime_status
        from agent.modules.workspaces.runtime import runtime_observation
        state = await workspace_runtime_status(ref)
        if runtime_observation(ref.backend, state).needs_replacement:
            return WorkspaceUnavailableError("The execution runtime no longer exists.", backend=ref.backend, locator=ref.locator)
        if state == "started":
            return FileNotFoundError("Workspace path not found.")
        return error
    return ValueError("Invalid workspace request.") if invalid else error


@dataclass(frozen=True)
class WorkspaceReadResult(Generic[T]):
    data: T
    data_source: Literal["live", "saved"]
    runtime_generation: int | str | None = None
    saved_revision: str | None = None
    last_saved_at: str | None = None
    fallback_reason: str | None = None

    def metadata(self) -> dict:
        return {"data_source": self.data_source, "stale": self.data_source == "saved",
                "runtime_generation": self.runtime_generation, "saved_revision": self.saved_revision,
                "last_saved_at": self.last_saved_at, "fallback_reason": self.fallback_reason}

    def legacy(self):
        return dict(self.data, **self.metadata()) if isinstance(self.data, dict) else self.data

    def headers(self) -> dict[str, str]:
        return {key: str(value) for key, value in {
            "X-Workspace-Data-Source": self.data_source,
            "X-Workspace-Generation": self.runtime_generation,
            "X-Workspace-Saved-Revision": self.saved_revision,
            "X-Workspace-Fallback-Reason": self.fallback_reason,
        }.items() if value is not None}


class WorkspaceReadService:
    async def read(self, facade, name, *args, consistency: ReadConsistency = "saved_ok", recover=False, **kwargs):
        if name not in READS or consistency not in {"live_required", "saved_ok"}:
            raise ValueError("Unsupported workspace read operation or consistency.")
        stack = AsyncExitStack()
        try:
            result = await self._read(facade, name, *args, stack=stack, consistency=consistency, recover=recover, **kwargs)
            if name != "stream_bytes":
                await stack.aclose()
            return result
        except BaseException as exc:
            with anyio.CancelScope(shield=True):
                await stack.__aexit__(type(exc), exc, exc.__traceback__)
            raise

    async def _read(self, facade, name, *args, stack, consistency, recover, **kwargs):
        from agent.modules.workspaces import create_workspace_runtime_backend
        from agent.modules.workspaces.host_mirror import workspace_is_running

        # Validate and normalize before selecting a source, including during outages.
        args = list(args)
        if args and name in PATH_FIRST:
            args[0] = facade._relative_path(args[0])
        for key in {"path", "file_path"} & kwargs.keys():
            kwargs[key] = facade._relative_path(kwargs[key])
        generation = await facade._read_generation()
        reason = "runtime_unavailable"
        try:
            if recover:
                await facade._recover_read()
                generation = await facade._read_generation()
            if facade._read_status not in {"ready", "stopped"}:
                raise WorkspaceUnavailableError("The live workspace is not ready.")
            if not await workspace_is_running(facade.ref):
                raise WorkspaceUnavailableError("The live workspace is unavailable.")
            ref = facade.ref.model_copy(deep=True)
            ref.metadata["_require_running"] = True
            backend = await create_workspace_runtime_backend(ref)
            if name == "stream_bytes":
                result = await WorkspaceByteStream.open(backend.stream_bytes(*args, **kwargs),
                                                        stack=stack, check=facade._read_generation)
            else:
                result = await getattr(backend, name)(*args, **kwargs)
            await facade._read_generation()
            return WorkspaceReadResult(result, "live", generation)
        except Exception as exc:
            normalized = await provider_read_error(facade.ref, exc)
            if consistency == "live_required" or not isinstance(normalized, (WorkspaceUnavailableError, TimeoutError, ConnectionError)):
                if normalized is exc:
                    raise
                raise normalized from exc
            reason = "provider_unavailable" if isinstance(normalized, (TimeoutError, ConnectionError)) else "runtime_unavailable"

        view = await stack.enter_async_context(facade._saved_view())
        backend = await create_workspace_runtime_backend(view)
        if name == "stream_bytes":
            result = await WorkspaceByteStream.open(backend.stream_bytes(*args, **kwargs),
                                                    stack=stack, check=facade._read_generation)
        elif name in {"changes", "diff"} and not (Path(view.locator) / ".git").exists():
            result = {"root": view.locator, "is_git_repo": False, "message": "Workspace is not a Git repository."}
            if name == "changes":
                result["changes"] = []
            else:
                result.update(path=str(Path(view.locator) / (args[0] if args else kwargs["path"])),
                              status="", diff="", truncated=False)
        else:
            result = await getattr(backend, name)(*args, **kwargs)
        await facade._read_generation()
        return WorkspaceReadResult(facade._present_paths(result, view.locator), "saved", generation,
                                   view.metadata["saved_revision"], view.metadata.get("last_saved_at"), reason)
