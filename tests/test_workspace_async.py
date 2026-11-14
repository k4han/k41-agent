from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agent.delivery.http.dashboard.routes.workspace import router
from agent.modules.workspaces import workspace_ref_from_local_path
from agent.modules.workspaces.local_backend import GIT_STATUS_CACHE_TTL, LocalWorkspaceBackend
from agent.modules.workspaces import service as workspace_service


def git_output(args: list[str], root: Path) -> str:
    if args[0] == "rev-parse":
        return f"{root}\n"
    if "status" in args:
        return " M notes.txt\0"
    if "--cached" in args:
        return ""
    if "--numstat" in args:
        return "1\t1\tnotes.txt\n"
    return "diff --git a/notes.txt b/notes.txt\n-old\n+new\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["tree", "changes", "diff"])
async def test_workspace_requests_allow_health_checks_during_blocking_io(
    operation, isolated_container, tmp_path, monkeypatch,
):
    (tmp_path / "notes.txt").write_text("new\n", encoding="utf-8")
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    started = asyncio.Event()
    release = threading.Event()
    io_threads: list[int] = []

    def block_io():
        io_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(started.set)
        if not release.wait(timeout=5):
            raise TimeoutError("Blocking workspace operation was not released.")

    original_iterdir = Path.iterdir

    def slow_iterdir(path):
        if path == tmp_path:
            block_io()
        return original_iterdir(path)

    def slow_git(args, *, cwd):
        assert cwd == tmp_path
        block_io()
        return git_output(args, tmp_path)

    if operation == "tree":
        monkeypatch.setattr(Path, "iterdir", slow_iterdir)
    else:
        monkeypatch.setattr(workspace_service, "_run_git", slow_git)

    app = FastAPI()
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        params = {"locator": str(tmp_path)}
        if operation == "diff":
            params["path"] = "notes.txt"
        request_task = asyncio.create_task(
            client.get(f"/dashboard-api/workspace/{operation}", params=params),
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            assert not request_task.done()
            health_response = await asyncio.wait_for(client.get("/health"), timeout=1)
            assert health_response.status_code == 200
            assert health_response.json() == {"status": "ok"}
        finally:
            release.set()
            response = await asyncio.wait_for(request_task, timeout=2)

    assert response.status_code == 200
    assert io_threads and all(thread != loop_thread for thread in io_threads)
    data = response.json()
    if operation == "tree":
        assert data["entries"][0]["name"] == "notes.txt"
    elif operation == "changes":
        assert data["changes"][0]["status"] == "modified"
        assert data["changes"][0]["additions"] == 1
        assert data["changes"][0]["deletions"] == 1
    else:
        assert data["status"] == "modified"
        assert "+new" in data["diff"]


@pytest.mark.asyncio
async def test_local_git_status_cache_is_reused_and_expires(tmp_path, monkeypatch):
    backend = LocalWorkspaceBackend(workspace_ref_from_local_path(str(tmp_path)))
    status_calls = []

    def run_git(args, *, cwd):
        if "status" in args:
            status_calls.append(args)
        return git_output(args, tmp_path)

    monkeypatch.setattr(workspace_service, "_run_git", run_git)
    changes = await backend.changes()
    diff = await backend.diff("notes.txt")
    assert len(status_calls) == 1
    assert diff["status"] == changes["changes"][0]["status"] == "modified"

    backend._git_status_cache_ts -= GIT_STATUS_CACHE_TTL + 1
    diff = await backend.diff("notes.txt")
    assert len(status_calls) == 2
    assert diff["status"] == "modified"
