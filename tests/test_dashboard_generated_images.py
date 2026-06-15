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


def test_generated_image_endpoint_rejects_non_image_extension(
    client: TestClient,
    tmp_path: Path,
) -> None:
    text_path = tmp_path / "sample.txt"
    text_path.write_text("not an image", encoding="utf-8")

    response = client.get("/dashboard-api/generated-images/sample.txt")

    assert response.status_code == 404
