"""Project instruction HTTP contracts and portable workspace behavior."""

import hashlib
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent.delivery.http.dashboard.routes import projects as routes
from agent.modules.projects import resources
from agent.modules.skills import package_io
from agent.modules.skills.package_worker import run_operation
from agent.modules.workspaces import WorkspaceUnavailableError
from agent.modules.workspaces.constants import MAX_FILE_BYTES


@pytest.fixture
def project_client(tmp_path, isolated_container, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    project = {"id": 1, "status": "removed", "workspace": {
        "execution": {"backend": "local", "locator": str(root), "metadata": {}},
    }}

    async def get_project(project_id):
        if project_id != 1:
            raise KeyError("Project not found.")
        return project

    monkeypatch.setattr(resources, "get_project", get_project)
    monkeypatch.setattr(routes, "get_project", get_project)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client, root, project


def test_detail_and_missing_instructions_do_not_create_files(project_client):
    client, root, project = project_client
    assert client.get("/dashboard-api/projects/1").json() == project
    assert client.get("/dashboard-api/projects/99").status_code == 404
    assert client.get("/dashboard-api/projects/99/instructions").status_code == 404
    assert client.put("/dashboard-api/projects/99/instructions", json={"content": "", "expected_version": None}).status_code == 404
    (root / "nested").mkdir()
    (root / "nested" / "AGENTS.md").write_text("Nested instructions")
    (root / "CLAUDE.md").write_text("Other instructions")
    result = client.get("/dashboard-api/projects/1/instructions")
    assert result.json() == {"path": "AGENTS.md", "exists": False, "content": "", "version": None}
    assert not (root / "AGENTS.md").exists()


def test_create_update_empty_and_external_conflicts(project_client):
    client, root, project = project_client
    url = "/dashboard-api/projects/1/instructions"
    created = client.put(url, json={"content": "# Instructions\n", "expected_version": None})
    assert created.status_code == 200, created.text
    document = created.json()
    assert document["version"] == hashlib.sha256(b"# Instructions\n").hexdigest()
    assert client.get(url).json() == document
    assert client.put(url, json={"content": "Overwrite", "expected_version": None}).status_code == 409
    (root / "AGENTS.md").write_text("Changed externally", encoding="utf-8")
    assert client.put(url, json={"content": "Stale", "expected_version": document["version"]}).status_code == 409
    current = client.get(url).json()
    empty = client.put(url, json={"content": "", "expected_version": current["version"]})
    assert empty.status_code == 200
    assert empty.json()["exists"] and empty.json()["content"] == ""
    assert (root / "AGENTS.md").read_bytes() == b""
    (root / "AGENTS.md").unlink()
    assert client.put(url, json={"content": "Lost", "expected_version": empty.json()["version"]}).status_code == 409
    assert not (root / "AGENTS.md").exists()
    assert project["status"] == "removed"


@pytest.mark.parametrize("body", [
    {"content": "Missing version"},
    {"content": "Invalid version", "expected_version": "stale"},
    {"content": "Extra path", "expected_version": None, "path": "other.md"},
    {"content": "Extra workspace", "expected_version": None, "workspace": "/elsewhere"},
])
def test_write_contract_rejects_unscoped_inputs(project_client, body):
    client, root, _ = project_client
    assert client.put("/dashboard-api/projects/1/instructions", json=body).status_code == 422
    assert not (root / "AGENTS.md").exists()


@pytest.mark.parametrize("raw", [b"\xffinvalid", b"text\x00binary", b"x" * (MAX_FILE_BYTES + 1)],
                         ids=["invalid-utf8", "null-bytes", "oversized"])
def test_invalid_or_oversized_document_cannot_be_edited(project_client, raw):
    client, root, _ = project_client
    (root / "AGENTS.md").write_bytes(raw)
    assert client.get("/dashboard-api/projects/1/instructions").status_code == 400
    assert (root / "AGENTS.md").read_bytes() == raw


def test_write_validates_utf8_byte_limit_and_null_bytes(project_client):
    client, root, _ = project_client
    url = "/dashboard-api/projects/1/instructions"
    for content in ("\u00e9" * (MAX_FILE_BYTES // 2 + 1), "bad\x00content", "bad\ud800content"):
        assert client.put(url, content=json.dumps({"content": content, "expected_version": None}),
                          headers={"Content-Type": "application/json"}).status_code == 400
    assert not (root / "AGENTS.md").exists()


def test_bom_and_exact_version(project_client):
    client, root, _ = project_client
    raw = b"\xef\xbb\xbf# Guide\r\n"
    (root / "AGENTS.md").write_bytes(raw)
    result = client.get("/dashboard-api/projects/1/instructions").json()
    assert result["content"] == "# Guide\r\n"
    assert result["version"] == hashlib.sha256(raw).hexdigest()
    assert client.put("/dashboard-api/projects/1/instructions", json={"content": "New", "expected_version": result["version"]}).status_code == 200


def test_missing_workspace_is_not_recreated(project_client):
    client, root, _ = project_client
    root.rmdir()
    url = "/dashboard-api/projects/1/instructions"
    assert client.get(url).status_code == 410
    assert client.put(url, json={"content": "New", "expected_version": None}).status_code == 410
    assert not root.exists()


def test_instruction_symlink_cannot_escape_workspace(project_client, tmp_path):
    client, root, _ = project_client
    external = tmp_path / "outside.md"
    external.write_text("External", encoding="utf-8")
    try:
        (root / "AGENTS.md").symlink_to(external)
    except OSError:
        pytest.skip("Symbolic link creation is unavailable on this host.")
    url = "/dashboard-api/projects/1/instructions"
    assert client.get(url).status_code == 400
    assert client.put(url, json={"content": "Overwrite", "expected_version": None}).status_code == 400
    assert external.read_text(encoding="utf-8") == "External"


@pytest.mark.parametrize("backend", ["daytona", "modal"])
def test_remote_instructions_use_portable_worker_and_preflight(project_client, monkeypatch, backend):
    client, root, project = project_client
    ref = {"backend": backend, "locator": "sandbox-1", "metadata": {"root": root.as_posix()}}
    project["workspace"]["execution"] = ref
    operations = []
    uploads = []

    class Backend:
        def __init__(self):
            self.ref = SimpleNamespace(**ref)

        async def write_bytes(self, path, content):
            uploads.append(path)
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        async def read_bytes(self, path):
            return (root / path).read_bytes()

        async def execute(self, command, **kwargs):
            if command.startswith("test -d"):
                return SimpleNamespace(exit_code=0 if root.is_dir() else 1, output="")
            if command.startswith("rm -f"):
                for path in shlex.split(command)[3:]:
                    Path(path).unlink(missing_ok=True)
            else:
                _, _, request_path, response_path = shlex.split(command)
                request = json.loads(Path(request_path).read_bytes())
                operations.append(request["request"])
                try:
                    result = {"result": run_operation(request["workspace"], request["request"])}
                except Exception as exc:
                    result = {"error": {"type": type(exc).__name__, "message": str(exc)}}
                Path(response_path).write_text(json.dumps(result), encoding="utf-8")
            return SimpleNamespace(exit_code=0, output="")

    monkeypatch.setattr(package_io, "get_workspace_file_io", AsyncMock(return_value=Backend()))
    url = "/dashboard-api/projects/1/instructions"
    assert client.get(url).json()["exists"] is False
    created = client.put(url, json={"content": "# Cloud", "expected_version": None})
    assert created.status_code == 200, created.text
    assert client.get(url).json() == created.json()
    assert client.put(url, json={"content": "Conflict", "expected_version": None}).status_code == 409
    assert all(item["require_existing_workspace"] and item["path"] == "AGENTS.md" for item in operations)
    assert all(item["limits"]["max_file_bytes"] == MAX_FILE_BYTES for item in operations)
    root.rename(root.with_name("moved-project"))
    uploaded = len(uploads)
    assert client.get(url).status_code == 410
    assert client.put(url, json={"content": "Missing", "expected_version": None}).status_code == 410
    assert len(uploads) == uploaded and not root.exists()
    monkeypatch.setattr(package_io, "get_workspace_file_io", AsyncMock(side_effect=WorkspaceUnavailableError("Sandbox unavailable.")))
    assert client.get(url).status_code == 410
