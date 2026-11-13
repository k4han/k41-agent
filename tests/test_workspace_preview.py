import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent.delivery.http.dashboard.routes.workspace import router
from agent.modules.workspaces import WorkspaceRef
from agent.modules.workspaces.reads import WorkspaceByteStream


async def byte_chunks(data, *, chunk_size=64 * 1024):
    for offset in range(0, len(data), chunk_size):
        yield data[offset:offset + chunk_size]


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("name,media_type", [
    ("photo.PNG", "image/png"), ("drawing.svg", "image/svg+xml"),
    ("report.pdf", "application/pdf"), ("music.mp3", "audio/mpeg"),
    ("movie.mp4", "video/mp4"),
])
def test_inline_preview_preserves_bytes_and_supports_ranges(client, tmp_path, name, media_type):
    data = b"\x00\xffbinary" * 50_000
    (tmp_path / name).write_bytes(data)
    params = {"locator": str(tmp_path), "path": name}
    response = client.get("/dashboard-api/workspace/preview", params=params)
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == media_type
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    if name.endswith("svg"):
        assert "sandbox" in response.headers["content-security-policy"]
    partial = client.get("/dashboard-api/workspace/preview", params=params, headers={"Range": "bytes=2-9"})
    assert partial.status_code == 206
    assert partial.content == data[2:10]


def test_preview_rejects_active_content_and_outside_paths(client, tmp_path):
    (tmp_path / "page.html").write_text("<script>alert(1)</script>")
    for name in ("page.html", "program.js", "archive.zip"):
        response = client.get("/dashboard-api/workspace/preview", params={"locator": str(tmp_path), "path": name})
        assert response.status_code == 415
    response = client.get("/dashboard-api/workspace/preview", params={"locator": str(tmp_path), "path": "../outside.png"})
    assert response.status_code == 400
    response = client.get("/dashboard-api/workspace/preview", params={"locator": str(tmp_path), "path": "missing.png"})
    assert response.status_code == 404


def test_download_preserves_unicode_names_and_binary_content(client, tmp_path):
    name = 'report "final".pdf' if os.name != "nt" else "report final.pdf"
    name = "\u00e9 " + name
    (tmp_path / name).write_bytes(b"\xff\x00\x80")
    response = client.get("/dashboard-api/workspace/download", params={"locator": str(tmp_path), "path": name})
    assert response.status_code == 200
    assert response.content == b"\xff\x00\x80"
    assert response.headers["content-disposition"].startswith("attachment; filename*=UTF-8''%C3%A9%20")


@pytest.mark.parametrize("content", [b"first", b"\x00first"])
def test_file_metadata_tracks_same_size_updates_without_tree_refresh(client, tmp_path, content):
    directory = tmp_path / "nested"
    directory.mkdir()
    image = directory / "image.svg"
    image.write_bytes(content)
    os.utime(image, (1_700_000_000, 1_700_000_000))
    params = {"locator": str(tmp_path), "path": "nested/image.svg"}
    first = client.get("/dashboard-api/workspace/file", params=params)
    assert first.status_code == 200
    assert first.json()["modified_at"] == image.stat().st_mtime

    image.write_bytes(content.replace(b"first", b"other"))
    os.utime(image, (1_700_000_002, 1_700_000_002))
    second = client.get("/dashboard-api/workspace/file", params=params)
    assert second.status_code == 200
    assert second.json()["size"] == first.json()["size"]
    assert second.json()["modified_at"] == image.stat().st_mtime
    assert second.json()["modified_at"] != first.json()["modified_at"]


@pytest.mark.parametrize("backend", ["daytona", "modal"])
def test_remote_preview_and_download_use_public_binary_stream(client, monkeypatch, backend):
    import agent.delivery.http.dashboard.routes.workspace as routes
    import agent.modules.workspaces as workspaces

    workspace = WorkspaceRef(backend=backend, locator="sandbox-test", metadata={"root": "/workspace"})
    monkeypatch.setattr(routes, "workspace_ref_from_request", AsyncMock(return_value=workspace))
    data = b"\x00\xff\x80" * 70_000
    reader = SimpleNamespace(stream_bytes=Mock(side_effect=lambda path: byte_chunks(data)),
                             read_bytes=AsyncMock(side_effect=AssertionError("Whole-file reads are forbidden")))
    getter = AsyncMock(return_value=reader)
    monkeypatch.setattr(workspaces, "get_workspace_file_io", getter)
    for action in ("preview", "download"):
        response = client.get(f"/dashboard-api/workspace/{action}", params={"thread_id": "test-thread", "path": "image.png"})
        assert response.status_code == 200
        assert response.content == data
        reader.stream_bytes.assert_called_with("image.png")
        reader.read_bytes.assert_not_awaited()
        getter.assert_awaited_with(workspace, thread_id="test-thread")


def test_saved_binary_responses_include_source_and_revision_headers(client, monkeypatch):
    import agent.delivery.http.dashboard.routes.workspace as routes
    import agent.modules.workspaces as workspaces
    from agent.modules.workspaces.reads import WorkspaceReadResult
    workspace = WorkspaceRef(backend="modal", locator="unavailable", metadata={"root": "/workspace"})
    monkeypatch.setattr(routes, "workspace_ref_from_request", AsyncMock(return_value=workspace))
    data = b"\x00\xffsaved"
    async def read_result(*args, **kwargs):
        return WorkspaceReadResult(await WorkspaceByteStream.open(byte_chunks(data)), "saved", 7,
                                   "revision", "timestamp", "provider_unavailable")
    reader = SimpleNamespace(read_result=AsyncMock(side_effect=read_result))
    monkeypatch.setattr(workspaces, "get_workspace_file_io", AsyncMock(return_value=reader))
    for action in ("preview", "download"):
        response = client.get(f"/dashboard-api/workspace/{action}", params={"thread_id": "thread", "path": "image.png"})
        assert response.status_code == 200 and response.content == data
        assert response.headers["x-workspace-data-source"] == "saved"
        assert response.headers["x-workspace-saved-revision"] == "revision"
        assert response.headers["x-workspace-generation"] == "7"
        reader.read_result.assert_awaited_with("stream_bytes", "image.png", consistency="saved_ok")


@pytest.mark.parametrize("error,status", [(FileNotFoundError("missing"), 404), (PermissionError("denied"), 403),
                                         (ValueError("invalid path"), 400)])
def test_remote_stream_errors_are_reported_before_response_headers(client, monkeypatch, error, status):
    import agent.delivery.http.dashboard.routes.workspace as routes
    import agent.modules.workspaces as workspaces

    async def chunks(path):
        raise error
        yield

    workspace = WorkspaceRef(backend="modal", locator="test", metadata={"root": "/workspace"})
    monkeypatch.setattr(routes, "workspace_ref_from_request", AsyncMock(return_value=workspace))
    monkeypatch.setattr(workspaces, "get_workspace_file_io", AsyncMock(return_value=SimpleNamespace(stream_bytes=chunks)))
    response = client.get("/dashboard-api/workspace/download", params={"path": "file.bin"})
    assert response.status_code == status
