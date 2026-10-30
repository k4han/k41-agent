"""Project credential storage, API, identity, and execution isolation contracts."""

import asyncio
import base64
import json
import os
import shlex
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.modules.projects import secrets, service as catalog
from agent.modules.projects.models import Project, ProjectSecret, ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.storage import LocalProjectStorage
from agent.modules.projects.workspaces import ProjectWorkspaceError, ProjectWorkspaceService
from agent.modules.agent_runtime.models import BackgroundTaskRecord
from agent.modules.conversations.models import ConversationThread
from agent.modules.tools.coding.contracts import CodingError, InvocationContext
from agent.modules.tools.coding.processes import ProcessManager, resolve_shell
from agent.modules.tools.coding.remote_worker import SandboxWorker
from agent.modules.tools.coding.storage import OutputStore
from agent.modules.tools.runtime.secret_policy import validate_secret_name
from agent.modules.tools.runtime.secret_redaction import SecretRedactor
from agent.modules.workspaces import WorkspaceRef, bind_workspace_ref
from agent.modules.workspaces.models import ThreadWorkspace
from agent.shared.infrastructure.secret_cipher import SecretCipher


@pytest_asyncio.fixture
async def secret_projects(tmp_path, isolated_container, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'projects.db').as_posix()}")
    async with engine.begin() as connection:
        for model in (Project, ProjectSecret, ProjectWorkspace, WorkspaceRuntime, ThreadWorkspace, ConversationThread, BackgroundTaskRecord):
            await connection.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        return maker()

    monkeypatch.setattr(catalog, "get_async_session", session)
    cipher = SecretCipher(tmp_path / "runtime_config.key")
    monkeypatch.setattr(secrets, "get_secret_cipher", lambda: cipher)
    projects = ProjectWorkspaceService(isolated_container, storage=LocalProjectStorage(tmp_path / "storage"))
    isolated_container._project_workspace_service = projects
    isolated_container._persistence_ready = True
    contexts = []
    async with maker() as session:
        for number in (1, 2):
            root = tmp_path / f"project-{number}"
            root.mkdir()
            ref = WorkspaceRef(locator=str(root))
            binding = bind_workspace_ref(ref)
            project = Project(key=binding.scope.key, label=f"Project {number}", status="active",
                              workspace_json=binding.model_dump_json(), migration_status="ready")
            session.add(project)
            await session.flush()
            workspace = ProjectWorkspace(project_id=project.id, identity_key="shared", source_json=ref.model_dump_json())
            session.add(workspace)
            await session.flush()
            project.local_workspace_id = workspace.id
            session.add(WorkspaceRuntime(project_workspace_id=workspace.id, execution_json=ref.model_dump_json(), generation=1))
            contexts.append(InvocationContext("default", str(root), f"thread-{number}", locator=str(root),
                                              project_id=project.id, project_workspace_id=workspace.id, runtime_generation=1))
        await session.commit()
    yield contexts, maker, projects, cipher
    await projects.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def secret_client(secret_projects):
    from agent.delivery.http.dashboard.router import router
    from agent.modules.admin_auth import get_current_admin
    app = FastAPI()
    app.include_router(router)

    async def admin():
        return "test-admin"

    app.dependency_overrides[get_current_admin] = admin
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_write_only_api_encrypts_and_scopes_credentials(secret_client, secret_projects):
    contexts, maker, _, _ = secret_projects
    first, second = [f"/dashboard-api/projects/{ctx.project_id}/secrets" for ctx in contexts]
    result = await secret_client.post(first, json={"name": "DEPLOY_TOKEN", "value": "private-first-token"})
    assert result.status_code == 201, result.text
    assert result.json()["enabled"] is True
    assert result.json()["updated_at"].endswith("+00:00")
    assert "private-first-token" not in result.text and "value" not in result.json()
    assert result.headers["cache-control"] == "no-store"
    assert (await secret_client.post(first, json={"name": "DEPLOY_TOKEN", "value": "duplicate-token"})).status_code == 409
    assert (await secret_client.post(second, json={"name": "DEPLOY_TOKEN", "value": "private-second-token"})).status_code == 201
    async with maker() as session:
        rows = list((await session.execute(select(ProjectSecret))).scalars())
        assert len(rows) == 2 and all("private-" not in row.encrypted_value for row in rows)
    assert await secrets.resolve_execution_secrets(contexts[0]) == {"DEPLOY_TOKEN": "private-first-token"}
    assert await secrets.resolve_execution_secrets(contexts[1]) == {"DEPLOY_TOKEN": "private-second-token"}
    assert (await secret_client.patch(first + "/DEPLOY_TOKEN", json={"enabled": False})).status_code == 200
    assert await secrets.resolve_execution_secrets(contexts[0]) == {}
    await secret_client.patch(first + "/DEPLOY_TOKEN", json={"enabled": True})
    assert await secrets.resolve_execution_secrets(contexts[0]) == {"DEPLOY_TOKEN": "private-first-token"}
    await secret_client.patch(first + "/DEPLOY_TOKEN", json={"value": "replacement-token"})
    assert await secrets.resolve_execution_secrets(contexts[0]) == {"DEPLOY_TOKEN": "replacement-token"}
    listing = await secret_client.get(first)
    assert listing.json()["secrets"][0]["name"] == "DEPLOY_TOKEN"
    assert listing.json()["secrets"][0]["updated_at"].endswith("+00:00")
    assert "replacement-token" not in listing.text
    assert (await secret_client.delete(first + "/DEPLOY_TOKEN")).status_code == 204
    assert (await secret_client.delete(first + "/DEPLOY_TOKEN")).status_code == 404
    assert await secrets.resolve_execution_secrets(contexts[0]) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"name": "bad-name", "value": "never-echo-this"},
    {"name": "PATH", "value": "never-echo-this"},
    {"name": "K41_WORKSPACE_ROOT", "value": "never-echo-this"},
    {"name": "TOKEN", "value": "never-echo-this\x00"},
    {"name": "TOKEN", "value": "never-echo-this", "extra": "never-echo-this"},
    {"name": "TOKEN", "value": {"nested": "never-echo-this"}},
    {"name": "TOKEN", "value": ""},
])
async def test_validation_never_echoes_request_values(secret_client, payload):
    result = await secret_client.post("/dashboard-api/projects/1/secrets", json=payload)
    assert result.status_code == 422
    assert "never-echo-this" not in result.text
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_missing_resources_and_invalid_updates_are_sanitized(secret_client):
    url = "/dashboard-api/projects/1/secrets"
    assert (await secret_client.get("/dashboard-api/projects/999/secrets")).status_code == 404
    assert (await secret_client.post("/dashboard-api/projects/999/secrets", json={"name": "TOKEN", "value": "private"})).status_code == 404
    assert (await secret_client.patch(url + "/MISSING", json={"enabled": True})).status_code == 404
    await secret_client.post(url, json={"name": "TOKEN", "value": "private"})
    for body in ({"value": ""}, {"value": None}, {"enabled": None}, {"name": "OTHER"}):
        assert (await secret_client.patch(url + "/TOKEN", json=body)).status_code == 422
    malformed = await secret_client.post(url, content='{"value":"never-echo-this",')
    assert malformed.status_code == 422 and "never-echo-this" not in malformed.text


@pytest.mark.asyncio
async def test_metadata_and_names_work_without_workspace_or_key(secret_client, secret_projects):
    contexts, maker, _, cipher = secret_projects
    context = contexts[0]
    await secrets.create_project_secret(context.project_id, "DEPLOY_TOKEN", "private-token")
    for status in ("archived", "removed", "active"):
        async with maker() as session:
            project = await session.get(Project, context.project_id)
            project.status = status
            await session.commit()
        assert (await secret_client.get(f"/dashboard-api/projects/{context.project_id}/secrets")).status_code == 200
    cipher.key_path.unlink()
    metadata = await secrets.list_project_secrets(context.project_id)
    assert metadata[0]["name"] == "DEPLOY_TOKEN"
    with pytest.raises(CodingError, match="could not be loaded"):
        await secrets.resolve_execution_secrets(context)
    assert not cipher.key_path.exists()
    result = await secret_client.post(f"/dashboard-api/projects/{context.project_id}/secrets", json={"name": "OTHER", "value": "private-other"})
    assert result.status_code == 503 and "private-other" not in result.text
    assert not cipher.key_path.exists()


@pytest.mark.asyncio
async def test_runtime_binding_and_replacement_are_checked(secret_projects):
    contexts, maker, _, _ = secret_projects
    context = contexts[0]
    await secrets.create_project_secret(context.project_id, "TOKEN", "runtime-token")
    for stale in (replace(context, runtime_generation=2), replace(context, locator="other"),
                  replace(context, project_workspace_id=contexts[1].project_workspace_id),
                  replace(context, runtime_generation=None), replace(context, workspace="/different-root")):
        with pytest.raises((CodingError, KeyError)):
            await secrets.resolve_execution_secrets(stale)
    assert await secrets.resolve_execution_secrets(replace(context, project_id=None, project_workspace_id=None)) == {}
    async with maker() as session:
        runtime = (await session.execute(select(WorkspaceRuntime).where(
            WorkspaceRuntime.project_workspace_id == context.project_workspace_id))).scalar_one()
        runtime.execution_json = WorkspaceRef(backend="modal", locator="new-sandbox", metadata={"root": "/workspace"}).model_dump_json()
        runtime.generation = 2
        await session.commit()
    updated = replace(context, backend="modal", locator="new-sandbox", workspace="/workspace", runtime_generation=2)
    assert await secrets.resolve_execution_secrets(updated) == {"TOKEN": "runtime-token"}


@pytest.mark.parametrize("name", ["PATH", "HOME", "SYSTEMROOT", "PYTHONUTF8", "K41_SECRET", "lowercase", "A-B"])
def test_reserved_and_nonportable_names_are_rejected(name):
    with pytest.raises(ValueError):
        validate_secret_name(name)


@pytest.mark.asyncio
async def test_prompt_names_refresh_without_decrypting_values(secret_projects, monkeypatch):
    contexts, _, _, cipher = secret_projects
    context = contexts[0]
    await secrets.create_project_secret(context.project_id, "TOKEN", "private-token")
    await secrets.create_project_secret(context.project_id, "DISABLED_TOKEN", "disabled-private", enabled=False)
    monkeypatch.setattr(cipher, "decrypt", lambda _: pytest.fail("Prompt generation must never decrypt credentials."))
    workspace = WorkspaceRef(locator=context.workspace)
    first = await secrets.project_secret_context(workspace)
    assert "TOKEN" in first and "private-token" not in first and "DISABLED_TOKEN" not in first
    await secrets.update_project_secret(context.project_id, "TOKEN", {"enabled": False})
    assert await secrets.project_secret_context(workspace) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("source,error", [
    ("binding", ProjectWorkspaceError("unlogged-error-detail", "stale_runtime")),
    ("binding", ProjectWorkspaceError("unlogged-error-detail", "migration_required")),
    ("binding", KeyError("unlogged-error-detail")),
    ("binding", NoResultFound("unlogged-error-detail")),
    ("binding", SQLAlchemyError("unlogged-error-detail")),
    ("metadata", SQLAlchemyError("unlogged-error-detail")),
    ("metadata", secrets.ProjectSecretError("unlogged-error-detail", 404)),
])
async def test_unavailable_secret_prompt_context_is_optional(secret_projects, monkeypatch, caplog, source, error):
    contexts, _, projects, _ = secret_projects
    if source == "binding":
        monkeypatch.setattr(projects, "find_binding", AsyncMock(side_effect=error))
    else:
        monkeypatch.setattr(secrets, "list_project_secrets", AsyncMock(side_effect=error))
    with caplog.at_level("WARNING", logger=secrets.__name__):
        assert await secrets.project_secret_context(WorkspaceRef(locator=contexts[0].workspace)) == ""
    assert "Project secret prompt context unavailable" in caplog.text
    assert "unlogged-error-detail" not in caplog.text


@pytest.mark.parametrize("values,text", [
    (["private-token"], "before private-token after"),
    (["token", "token-longer"], "token-longer token"),
    (["abc", "bcde"], "abcde"),
    (["unicode-\u03bb\nkey"], "unicode-\u03bb\nkey"),
])
def test_streaming_redaction_handles_every_split(values, text):
    for split in range(len(text) + 1):
        redactor = SecretRedactor(values)
        result = redactor.feed(text[:split]) + redactor.feed(text[split:]) + redactor.feed("", final=True)
        assert all(value not in result for value in values)
        assert "[REDACTED]" in result


def test_multiline_secret_redaction_handles_terminal_newline_translation():
    for value in ("line-one\nline-two", "line-one\r\nline-two"):
        redactor = SecretRedactor([value])
        output = value.replace("\n", "\r\n")
        result = "".join(redactor.feed(char) for char in output) + redactor.feed("", final=True)
        assert result == "[REDACTED]"


def python_command(source):
    encoded = base64.b64encode(source.encode()).decode()
    argument = f"import base64;exec(base64.b64decode('{encoded}'))"
    if os.name == "nt":
        return f'& "{sys.executable}" -c "{argument}"'
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(argument)}"


@pytest.mark.asyncio
async def test_process_isolation_and_redaction_before_retention(secret_projects, tmp_path, monkeypatch):
    contexts, _, _, _ = secret_projects
    monkeypatch.setenv("SERVER_API_KEY", "operator-only-key")
    manager = ProcessManager(OutputStore(tmp_path / "outputs"), register_sessions=False)
    try:
        for context, value in zip(contexts, ("first-private-token", "second-private-token")):
            await secrets.create_project_secret(context.project_id, "DEPLOY_TOKEN", value)
        async def start(context):
            source = ("import os,sys; key=os.environ['DEPLOY_TOKEN']; "
                      "assert 'SERVER_API_KEY' not in os.environ; "
                      "sys.stdout.write('start:'+key[:5]); sys.stdout.flush(); "
                      "sys.stdout.write(key[5:]+'\\n'+'x'*60000+key); "
                      "sys.stderr.write(key); sys.stderr.flush()")
            return await manager.start(context, python_command(source), Path(context.workspace), 30, resolve_shell(),
                                       execution_secrets=await secrets.resolve_execution_secrets(context))
        jobs = await asyncio.gather(*(start(context) for context in contexts))
        await asyncio.gather(*(job.finished.wait() for job in jobs))
        for job, raw in zip(jobs, ("first-private-token", "second-private-token")):
            assert job.process.returncode == 0
            assert job.secret_values == ()
            assert raw not in job.head.decode() and raw not in job.tail.decode()
            assert job.path is not None and raw.encode() not in job.path.read_bytes()
            result = await manager.observe(job, preview=True)
            assert raw not in json.dumps(result.to_dict()) and "[REDACTED]" in job.retained_plain()
        assert "DEPLOY_TOKEN" not in os.environ
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_running_process_keeps_snapshot_after_rotation(secret_projects, tmp_path):
    context = secret_projects[0][0]
    await secrets.create_project_secret(context.project_id, "TOKEN", "original-token")
    manager = ProcessManager(OutputStore(tmp_path / "outputs"), register_sessions=False)
    try:
        job = await manager.start(context, python_command("import os,sys; sys.stdin.readline(); print(os.environ['TOKEN'])"),
                                  Path(context.workspace), 30, resolve_shell(),
                                  execution_secrets=await secrets.resolve_execution_secrets(context))
        await secrets.update_project_secret(context.project_id, "TOKEN", {"value": "rotated-token"})
        assert await secrets.resolve_execution_secrets(context) == {"TOKEN": "rotated-token"}
        await secrets.delete_project_secret(context.project_id, "TOKEN")
        await manager.send(job, "continue\n")
        await job.finished.wait()
        assert job.process.returncode == 0 and "[REDACTED]" in job.retained_plain()
        assert "original-token" not in job.retained_plain()
        assert await secrets.resolve_execution_secrets(context) == {}
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["daytona", "modal"])
async def test_sandbox_worker_receives_private_execution_environment(backend, tmp_path):
    root = tmp_path / backend
    root.mkdir()
    worker = SandboxWorker(tmp_path / "outputs", str(root))
    context = InvocationContext("default", str(root), "thread", backend=backend, locator="sandbox")
    from dataclasses import asdict
    request = {"operation": "prepare", "name": "bash", "context": asdict(context), "values": {
        "command": python_command("import os; print('present' if os.environ.get('TOKEN') else 'absent'); print(os.environ['TOKEN'])"),
        "timeout_seconds": 30, "yield_time_ms": 0, "workdir": "."}}
    try:
        prepared = await worker.dispatch(request)
        from agent.modules.tools.coding.remote_worker import permission_identity
        request.update(operation="execute", execution_secrets={"TOKEN": "sandbox-private-token"},
                       authorized=[permission_identity(item["action"], item["resource"], item["metadata"])
                                   for item in prepared["permissions"]])
        result = await worker.dispatch(request)
        job = worker.processes.jobs[result["data"]["process_id"]]
        await job.finished.wait()
        assert job.process.returncode == 0
        assert "present" in job.retained_plain() and "[REDACTED]" in job.retained_plain()
        assert "sandbox-private-token" not in job.retained_plain()
    finally:
        await worker.processes.close()


@pytest.mark.asyncio
async def test_skill_collision_fails_before_start(tmp_path):
    manager = ProcessManager(OutputStore(tmp_path / "outputs"), register_sessions=False)
    context = InvocationContext("default", str(tmp_path), "thread", skill_environment=(("SERVICE_ROOT", "/skill"),))
    try:
        with pytest.raises(CodingError, match="conflicts"):
            await manager.start(context, "exit 0", tmp_path, 30, resolve_shell(), execution_secrets={"SERVICE_ROOT": "private"})
        assert manager.jobs == {}
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_coding_service_resolves_secrets_for_project_and_child_tasks(secret_projects, tmp_path, isolated_container):
    from agent.modules.tools.coding.adapter import SCHEMAS
    from agent.modules.tools.coding.models import ToolDefinition
    from agent.modules.tools.coding.service import CodingService
    context = secret_projects[0][0]
    maker = secret_projects[1]
    async with maker() as session:
        for thread_id in (context.thread_id, "background-task"):
            session.add(ThreadWorkspace(thread_id=thread_id, project_id=context.project_id,
                                        project_workspace_id=context.project_workspace_id))
        await session.commit()
    await secrets.create_project_secret(context.project_id, "TOKEN", "project-service-private")
    service = CodingService(tmp_path / "coding-outputs")
    isolated_container._coding_service = service

    async def execute(parsed, ctx, runtime):
        return await runtime.execute("bash", parsed.model_dump(), ctx)

    definition = ToolDefinition("bash", "bash", SCHEMAS["bash"], execute)
    try:
        for thread_id in (context.thread_id, "background-task", context.thread_id + ":sub:child"):
            result = await service.invoke(definition, {"command": python_command(
                "import os; assert os.environ['TOKEN']; print(os.environ['TOKEN'])"), "yield_time_ms": 1000},
                replace(context, thread_id=thread_id, project_id=None, project_workspace_id=None, runtime_generation=None))
            assert result.error is None, result
            job = service.processes.jobs[result.data["process_id"]]
            await job.finished.wait()
            assert job.process.returncode == 0 and "[REDACTED]" in job.retained_plain()
            assert "project-service-private" not in json.dumps(result.model_dump())
    finally:
        await service.processes.close()


@pytest.mark.asyncio
async def test_management_requires_admin_authentication():
    from agent.delivery.http.dashboard.router import router
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for method, url, body in (
            ("GET", "/dashboard-api/projects/1/secrets", None),
            ("POST", "/dashboard-api/projects/1/secrets", {"name": "TOKEN", "value": "private"}),
            ("PATCH", "/dashboard-api/projects/1/secrets/TOKEN", {"enabled": False}),
            ("DELETE", "/dashboard-api/projects/1/secrets/TOKEN", None),
        ):
            assert (await client.request(method, url, json=body)).status_code == 401


@pytest.mark.asyncio
async def test_denied_command_never_resolves_credentials(tmp_path, isolated_container, monkeypatch):
    from unittest.mock import AsyncMock
    from agent.modules.tools.coding.adapter import SCHEMAS
    from agent.modules.tools.coding.models import PermissionRule, ToolDefinition
    from agent.modules.tools.coding.service import CodingService
    service = CodingService(tmp_path / "output")
    isolated_container._coding_service = service
    resolver = AsyncMock(side_effect=AssertionError("Denied commands must not load credentials."))
    monkeypatch.setattr(service, "resolve_process_secrets", resolver)
    context = InvocationContext("default", str(tmp_path), "thread", permission_rules=(
        PermissionRule(action="shell", resource="*", effect="deny"),))

    async def execute(parsed, ctx, runtime):
        return await runtime.execute("bash", parsed.model_dump(), ctx)

    try:
        result = await service.invoke(ToolDefinition("bash", "bash", SCHEMAS["bash"], execute), {"command": "exit 0"}, context)
        assert result.error.code == "permission_denied"
        resolver.assert_not_awaited()
        assert service.processes.jobs == {}
    finally:
        await service.processes.close()
