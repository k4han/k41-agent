"""Inspect workspace path and read contracts without calling cloud providers."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path.cwd()))

from agent.modules.projects.backend import DeferredWorkspaceBackend
from agent.modules.projects.local_sync import _plan
from agent.modules.workspaces.host_mirror import comparison, HostMirrorService
from agent.modules.workspaces.posix_utils import resolve_remote_path
from agent.modules.workspaces.refs import WorkspaceRef


def result_of(function, *args):
    try:
        return {"value": function(*args)}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}


async def main():
    execution = WorkspaceRef(backend="modal", locator="synthetic-runtime", metadata={"root": "/workspace"})
    binding = SimpleNamespace(execution=execution, project_id=1, project_workspace_id=1, runtime_generation=1)

    @asynccontextmanager
    async def saved_view(*args):
        yield WorkspaceRef(locator=str(Path.cwd() / "synthetic-saved-view"), metadata={
            "saved_revision": "synthetic-revision", "last_saved_at": "2026-10-25T00:00:00Z"})

    service = SimpleNamespace(
        rows=AsyncMock(return_value=(None, None, SimpleNamespace(generation=1, status="ready"))),
        saved_view=saved_view,
    )
    browser = DeferredWorkspaceBackend(binding, service)
    result = {"synthetic_inputs_only": True, "real_provider_calls": 0}
    result["path_resolution"] = [
        {"input": value,
         "provider_helper": result_of(resolve_remote_path, "/workspace", value),
         "saved_browser": result_of(browser._relative_path, value)}
        for value in ("src/main.py", "../outside.txt", "/other/file.txt", r"C:\project\file.txt")
    ]

    async def factory(ref, **kwargs):
        if ref.backend != "local":
            return SimpleNamespace(
                read_text=AsyncMock(side_effect=FileNotFoundError("Deleted in live runtime")),
                file=AsyncMock(side_effect=FileNotFoundError("Deleted in live runtime")),
            )
        return SimpleNamespace(
            read_text=AsyncMock(return_value="SYNTHETIC_SAVED_OLD_CONTENT"),
            file=AsyncMock(return_value={"content": "SYNTHETIC_SAVED_OLD_CONTENT"}),
        )

    with patch("agent.modules.workspaces.host_mirror.workspace_is_running", AsyncMock(return_value=True)), \
         patch("agent.modules.workspaces.create_workspace_runtime_backend", factory):
        result["live_missing_file"] = {
            "text_result": await browser.read_text("/workspace/deleted.txt"),
            "structured_result": await browser.file("/workspace/deleted.txt"),
        }

    def entry(content):
        return {"type": "file", "mode": 0o644, "checksum": content, "size": len(content)}

    base = {"version": 1, "entries": {"file.txt": entry("base")}}
    desired = {"version": 1, "entries": {"file.txt": entry("base"), "added.txt": entry("remote")}}
    current = {"version": 1, "entries": {"file.txt": entry("host")}}
    root = Path.cwd() / "synthetic-unused-comparison-root"
    result["independent_edits"] = {
        "manual_local_sync": _plan(base, desired, current, root),
        "automatic_host_mirror": comparison(base, desired, current, root),
    }
    source = WorkspaceRef(locator=str(Path.cwd() / "synthetic-original-project"))
    workspace = SimpleNamespace(id=1, source_json=source.model_dump_json(), host_mirror_json="{}")
    runtime = SimpleNamespace(execution_json=execution.model_dump_json())
    mirror = SimpleNamespace(service=SimpleNamespace(runtime_root=Path.cwd() / "synthetic-managed-runtimes"))
    result["cloud_mirror_uses_original_local_path"] = (
        HostMirrorService.project_path(mirror, workspace, runtime) == Path(source.locator)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
