from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent.delivery.http.dashboard.router import router
from agent.delivery.http.dashboard.routes import generated_images
from agent.modules.admin_auth import get_current_admin


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
