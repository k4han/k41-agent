from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent.delivery.http.dashboard.router import router
from agent.delivery.http.dashboard.routes import generated_images
from agent.modules.admin_auth import get_current_admin
from agent.modules.workspaces import WorkspaceRef, derive_workspace_scope
import agent.modules.tools.runtime.thread_storage as thread_storage


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(generated_images, "GENERATED_IMAGES_DIR", tmp_path)

    app = FastAPI()
    app.include_router(router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app)


def test_generated_image_endpoint_serves_image(client: TestClient, tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    response = client.get("/dashboard-api/generated-images/sample.png")

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"
    assert response.headers["content-type"].startswith("image/png")


def test_generated_image_endpoint_serves_workspace_image_for_thread(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    workspace = WorkspaceRef(locator=str((tmp_path / "workspace").resolve()))
    image_dir = thread_storage.generated_images_dir_for_workspace(
        derive_workspace_scope(workspace),
    )
    image_path = image_dir / "thread.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def fake_workspace_ref_for_thread(
        thread_id: str,
        *,
        include_default: bool = True,
    ) -> WorkspaceRef | None:
        assert include_default is False
        return workspace

    monkeypatch.setattr(
        generated_images,
        "workspace_ref_for_thread",
        fake_workspace_ref_for_thread,
    )

    response = client.get(
        "/dashboard-api/generated-images/thread.png",
        params={"thread_id": "chat_user:sub:worker:abcd1234"},
    )

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"
    assert response.headers["content-type"].startswith("image/png")


def test_generated_image_endpoint_falls_back_when_thread_has_no_workspace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "global.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def fake_workspace_ref_for_thread(
        thread_id: str,
        *,
        include_default: bool = True,
    ) -> WorkspaceRef | None:
        assert include_default is False
        return None

    monkeypatch.setattr(
        generated_images,
        "workspace_ref_for_thread",
        fake_workspace_ref_for_thread,
    )

    response = client.get(
        "/dashboard-api/generated-images/global.png",
        params={"thread_id": "chat_user:sub:worker:missing"},
    )

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"


def test_generated_image_endpoint_rejects_path_traversal(
    client: TestClient,
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"outside")

    response = client.get("/dashboard-api/generated-images/../outside.png")

    assert response.status_code == 404


def test_generated_image_endpoint_serves_binary_files(
    client: TestClient,
    tmp_path: Path,
) -> None:
    excel_path = tmp_path / "data.xlsx"
    excel_path.write_bytes(b"PK\x03\x04excel_bytes")

    response = client.get("/dashboard-api/generated-images/data.xlsx")

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04excel_bytes"


def test_storage_files_endpoint_serves_uploads_and_assets(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    workspace = WorkspaceRef(locator=str((tmp_path / "workspace").resolve()))
    upload_dir = thread_storage.ensure_workspace_storage_root(
        derive_workspace_scope(workspace)
    ) / "uploads"
    upload_path = upload_dir / "report.pdf"
    upload_path.write_bytes(b"%PDF-1.4 sample")

    async def fake_workspace_ref_for_thread(
        thread_id: str,
        *,
        include_default: bool = True,
    ) -> WorkspaceRef | None:
        assert include_default is False
        return workspace

    monkeypatch.setattr(
        generated_images,
        "workspace_ref_for_thread",
        fake_workspace_ref_for_thread,
    )

    response = client.get(
        "/dashboard-api/storage-files/uploads/report.pdf",
        params={"thread_id": "chat_user:sub:worker:abcd1234"},
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 sample"
    assert response.headers["content-type"].startswith("application/pdf")


def test_generated_image_endpoint_rejects_disallowed_extension(
    client: TestClient,
    tmp_path: Path,
) -> None:
    exe_path = tmp_path / "sample.exe"
    exe_path.write_bytes(b"MZ_executable")

    response = client.get("/dashboard-api/generated-images/sample.exe")

    assert response.status_code == 403


def test_workspace_download_endpoint_serves_binary_file(
    client: TestClient,
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "my_project"
    workspace_dir.mkdir()
    excel_file = workspace_dir / "financials.xlsx"
    excel_file.write_bytes(b"PK\x03\x04excel_binary_content")

    response = client.get(
        "/dashboard-api/workspace/download",
        params={
            "backend": "local",
            "locator": str(workspace_dir),
            "path": "financials.xlsx",
        },
    )

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04excel_binary_content"
    assert response.headers["content-disposition"] == 'inline; filename="financials.xlsx"' or 'attachment' in response.headers.get("content-disposition", "") or "financials.xlsx" in response.headers.get("content-disposition", "")
