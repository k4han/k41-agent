"""End-to-end package API behavior without cloud credentials."""

import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent.delivery.http.dashboard.routes.skill_packages import router
from agent.modules.skills.repository import FilesystemSkillRepository

DOCUMENT = "---\nname: demo\ndescription: A demo.\ncustom:\n  keep: true\n---\n# Guide\n"


@pytest.fixture
def client(tmp_path, isolated_container):
    isolated_container._skill_repository = FilesystemSkillRepository(tmp_path / "skills")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def create(client):
    result = client.post("/dashboard-api/skill-packages", json={"name": "demo", "content": DOCUMENT})
    assert result.status_code == 200, result.text
    return client.get("/dashboard-api/skill-packages").json()["packages"][0]["id"]


def test_package_document_roundtrip_and_optimistic_writes(client):
    identity = create(client)
    prefix = f"/dashboard-api/skill-packages/{identity}"
    detail = client.get(prefix).json()["package"]
    result = client.put(prefix + "/document", json={"expected_version": detail["file_version"], "frontmatter": {"description": "Changed"}, "body": "New instructions"})
    assert result.status_code == 200, result.text
    changed = client.get(prefix).json()["package"]
    assert changed["frontmatter"]["custom"] == {"keep": True}
    assert changed["body"] == "New instructions"
    assert client.put(prefix + "/document", json={"expected_version": detail["file_version"], "body": "Stale"}).status_code == 409


def test_package_binary_files_and_full_delete(client):
    identity = create(client)
    prefix = f"/dashboard-api/skill-packages/{identity}"
    payload = bytes(range(256))
    assert client.put(prefix + "/files/assets/template.bin", content=payload).status_code == 200
    info = client.get(prefix + "/files/assets/template.bin").json()
    assert info["content"] is None
    assert client.get(prefix + "/files/assets/template.bin?download=true").content == payload
    assert client.put(prefix + "/files/assets/template.bin", content=b"stale").status_code == 409
    assert client.put(prefix + "/files/assets/template.bin", content=b"new", headers={"If-Match": info["version"]}).status_code == 200
    assert client.post(prefix + "/files", json={"action": "mkdir", "path": "empty"}).status_code == 200
    exported = client.get(prefix + "/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert archive.read("assets/template.bin") == b"new"
        assert "empty/" in archive.namelist()
    assert client.delete(prefix).status_code == 200
    assert client.get("/dashboard-api/skill-packages").json()["packages"] == []


def test_project_scope_isolation(client, tmp_path):
    first = str(tmp_path / "first")
    second = str(tmp_path / "second")
    result = client.post("/dashboard-api/skill-packages", json={"name": "demo", "content": DOCUMENT, "scope": "project", "workspace": first})
    assert result.status_code == 200, result.text
    identity = client.get("/dashboard-api/skill-packages", params={"workspace": first, "scope": "project"}).json()["packages"][0]["id"]
    assert client.get(f"/dashboard-api/skill-packages/{identity}", params={"workspace": second}).status_code == 404
    assert client.get(f"/dashboard-api/skill-packages/{identity}", params={"workspace": first}).status_code == 200


def test_zip_preview_import_conflicts(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("demo/SKILL.md", DOCUMENT)
        archive.writestr("demo/data.bin", b"\x00\xff")
    preview = client.post("/dashboard-api/skill-packages/imports/preview", files={"file": ("demo.zip", buffer.getvalue(), "application/zip")})
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_id"]
    assert client.post("/dashboard-api/skill-packages/imports", json={"preview_id": token}).status_code == 200
    preview = client.post("/dashboard-api/skill-packages/imports/preview", files={"file": ("demo.zip", buffer.getvalue(), "application/zip")})
    assert preview.json()["skills"][0]["conflict"]
    token = preview.json()["preview_id"]
    assert client.post("/dashboard-api/skill-packages/imports", json={"preview_id": token}).status_code == 409
    assert client.post("/dashboard-api/skill-packages/imports", json={"preview_id": token, "overwrite": True}).status_code == 200


def test_rename_and_enabled_state_persist(client):
    identity = create(client)
    prefix = f"/dashboard-api/skill-packages/{identity}"
    detail = client.get(prefix).json()["package"]
    assert client.patch(prefix, json={"enabled": False}).status_code == 200
    assert not client.get("/dashboard-api/skill-packages").json()["packages"][0]["enabled"]
    assert client.patch(prefix, json={"name": "renamed", "expected_version": detail["file_version"]}).status_code == 200
    packages = client.get("/dashboard-api/skill-packages").json()["packages"]
    assert len(packages) == 1 and packages[0]["name"] == "renamed"
    assert not packages[0]["enabled"]


def test_resource_paths_are_confined(client):
    identity = create(client)
    prefix = f"/dashboard-api/skill-packages/{identity}"
    assert client.post(prefix + "/files", json={"action": "mkdir", "path": "../outside"}).status_code == 400
    assert client.post(prefix + "/files", json={"action": "rename", "path": "SKILL.md", "destination": "../outside", "expected_version": "stale"}).status_code in (400, 409)


def test_import_overwrite_checks_destination_version(client):
    identity = create(client)
    prefix = f"/dashboard-api/skill-packages/{identity}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", DOCUMENT)
    preview = client.post("/dashboard-api/skill-packages/imports/preview", files={"file": ("demo.zip", buffer.getvalue(), "application/zip")}).json()
    assert preview["skills"][0]["existing_version"]
    assert client.put(prefix + "/files/new.txt", content=b"concurrent edit").status_code == 200
    result = client.post("/dashboard-api/skill-packages/imports", json={"preview_id": preview["preview_id"], "overwrite": True})
    assert result.status_code == 409
    assert client.get(prefix + "/files/new.txt?download=true").content == b"concurrent edit"


def test_corrupt_zip_is_an_input_error(client):
    result = client.post("/dashboard-api/skill-packages/imports/preview", files={"file": ("broken.zip", b"broken", "application/zip")})
    assert result.status_code == 400
