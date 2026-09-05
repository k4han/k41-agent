import pytest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.delivery.http.dashboard.router import router as dashboard_router
from agent.modules.admin_auth import get_current_admin
from agent.modules.workspaces import (
    create_workspace_file,
    create_workspace_directory,
    get_workspace_entry_mutator,
    resolve_workspace_ref,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_current_admin] = lambda: "test_admin"
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_create_workspace_file_and_directory(tmp_path: Path):
    workspace = resolve_workspace_ref(str(tmp_path))
    mutator = await get_workspace_entry_mutator(workspace)

    # 1. Create directory
    dir_res = await mutator.create_directory(path="my_folder")
    assert dir_res["kind"] == "directory"
    assert dir_res["relative_path"] == "my_folder"
    assert (tmp_path / "my_folder").is_dir()

    # 2. Create nested file inside directory
    file_res = await mutator.create_file(
        path="my_folder/nested/hello.txt",
        content="Hello world!",
    )
    assert file_res["kind"] == "file"
    assert file_res["relative_path"] == "my_folder/nested/hello.txt"
    created_file = tmp_path / "my_folder" / "nested" / "hello.txt"
    assert created_file.is_file()
    assert created_file.read_text(encoding="utf-8") == "Hello world!"

    # 3. Duplicate creation raises FileExistsError
    with pytest.raises(FileExistsError):
        await mutator.create_file(path="my_folder/nested/hello.txt")

    with pytest.raises(FileExistsError):
        await mutator.create_directory(path="my_folder")

    # 4. Escaping workspace raises ValueError
    with pytest.raises(ValueError):
        await mutator.create_file(path="../escaped.txt")

    with pytest.raises(ValueError):
        await mutator.create_directory(path="../escaped_dir")

    # 5. Empty path raises ValueError
    with pytest.raises(ValueError):
        await mutator.create_file(path="")

    with pytest.raises(ValueError):
        await mutator.create_directory(path="")


def test_dashboard_workspace_create_entry_endpoint(client: TestClient, tmp_path: Path):
    workspace_payload = {
        "backend": "local",
        "locator": str(tmp_path),
    }

    # 1. Create folder via API
    resp_dir = client.post(
        "/dashboard-api/workspace/create-entry",
        json={
            "workspace": workspace_payload,
            "path": "docs",
            "kind": "directory",
        },
    )
    assert resp_dir.status_code == 200
    assert resp_dir.json()["kind"] == "directory"
    assert (tmp_path / "docs").is_dir()

    # 2. Create file via API
    resp_file = client.post(
        "/dashboard-api/workspace/create-entry",
        json={
            "workspace": workspace_payload,
            "path": "docs/readme.md",
            "kind": "file",
            "content": "# Test Readme",
        },
    )
    assert resp_file.status_code == 200
    assert resp_file.json()["kind"] == "file"
    created_file = tmp_path / "docs" / "readme.md"
    assert created_file.is_file()
    assert created_file.read_text(encoding="utf-8") == "# Test Readme"

    # 3. Error on duplicate
    resp_dup = client.post(
        "/dashboard-api/workspace/create-entry",
        json={
            "workspace": workspace_payload,
            "path": "docs/readme.md",
            "kind": "file",
        },
    )
    assert resp_dup.status_code in (400, 409)
