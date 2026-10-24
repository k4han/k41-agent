from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.delivery.http.dashboard.routes import projects as routes
from agent.modules.agent_runtime.models import BackgroundTaskRecord
from agent.modules.agent_runtime import repository as task_repository
from agent.modules.conversations import navigation
from agent.modules.conversations.models import ConversationThread
from agent.modules.projects import service
from agent.modules.projects.models import Project, ProjectWorkspace, WorkspaceRuntime
from agent.modules.projects.repository import cursor_condition, register_project
from agent.modules.workspaces import bind_workspace_ref, resolve_workspace_ref
from agent.modules.workspaces.models import ThreadWorkspace
from agent.modules.workspaces import repository as workspace_repository


@pytest_asyncio.fixture
async def catalog_db(tmp_path, monkeypatch, isolated_container):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'catalog.db').as_posix()}")
    async with engine.begin() as connection:
        for model in (ConversationThread, ThreadWorkspace, BackgroundTaskRecord, Project, ProjectWorkspace, WorkspaceRuntime):
            await connection.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def get_session():
        return maker()

    for module in (service, navigation, workspace_repository, task_repository):
        monkeypatch.setattr(module, "get_async_session", get_session)
    isolated_container._persistence_ready = True
    yield maker
    await engine.dispose()


def workspace(path, **metadata):
    return resolve_workspace_ref({"backend": "local", "locator": str(path), "metadata": metadata})


async def seed_thread(maker, thread_id, ref, *, kind="user", deleted=False, age=0, task=False):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=age)
    binding = bind_workspace_ref(ref)
    async with maker() as session:
        session.add(ConversationThread(thread_id=thread_id, kind=kind, platform="api", user_id="dashboard",
                                       created_at=now, updated_at=now, deleted_at=now if deleted else None))
        columns = dict(thread_id=thread_id, scope_key=binding.scope.key, scope_kind=binding.scope.kind,
                       execution_backend=ref.backend, execution_locator=ref.locator,
                       execution_label=ref.label, execution_metadata_json=json.dumps(ref.metadata))
        session.add(BackgroundTaskRecord(task_id=f"task-{thread_id}", **columns) if task else ThreadWorkspace(**columns))
        await session.commit()


@pytest.mark.asyncio
async def test_catalog_lifetime_rename_removal_and_explicit_restore(catalog_db, tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "keep.txt").write_text("retained", encoding="utf-8")
    ref = workspace(root)
    project = await service.add_project(ref, label="Custom name")
    assert await service.get_project(project["id"]) == project
    with pytest.raises(KeyError):
        await service.get_project(project["id"] + 100)
    assert (await navigation.list_conversation_projects())["projects"][0]["threads"] == []
    renamed = await service.update_project(project["id"], {"label": "Renamed", "is_pinned": True})
    assert renamed["key"] == project["key"]
    assert renamed["workspace"]["scope"]["label"] == "Renamed"
    assert renamed["workspace"]["scope"]["key"] == project["workspace"]["scope"]["key"]
    assert renamed["workspace"]["execution"]["metadata"]["project_scope"]["label"] == "Renamed"
    for field in ("project_id", "project_workspace_id", "runtime_generation"):
        assert renamed["workspace"][field] == project["workspace"][field]
    for field in ("backend", "locator"):
        assert renamed["workspace"]["execution"][field] == project["workspace"]["execution"][field]
    await service.update_project(project["id"], {"status": "removed"})
    await seed_thread(catalog_db, "retained", ref)
    await workspace_repository.ThreadWorkspaceRepository().upsert(thread_id="retained", workspace=ref)
    await service.import_existing_projects()
    assert (await navigation.list_conversation_projects())["projects"] == []
    removed = (await service.list_projects(status="removed"))["projects"][0]
    assert removed["label"] == "Renamed" and removed["is_pinned"] is True
    restored = await service.add_project(ref)
    assert restored["id"] == project["id"] and restored["label"] == "Renamed"
    assert restored["status"] == "active"
    assert (root / "keep.txt").read_text(encoding="utf-8") == "retained"
    async with catalog_db() as session:
        assert await session.scalar(select(func.count()).select_from(ConversationThread)) == 1
        await session.execute(update(ConversationThread).values(deleted_at=datetime.now(timezone.utc)))
        await session.commit()
    assert (await navigation.list_conversation_projects())["projects"][0]["threads"] == []


@pytest.mark.asyncio
async def test_backfill_precedence_latest_workspace_and_idempotence(catalog_db, tmp_path):
    old = workspace(tmp_path / "old", source="github", repository_id=42, repository_full_name="team/repo")
    recent = workspace(tmp_path / "recent", source="github", repository_id=42, repository_full_name="team/repo")
    await seed_thread(catalog_db, "old", old, age=1)
    await seed_thread(catalog_db, "recent", recent, age=2, task=True, kind="background")
    await seed_thread(catalog_db, "temporary", workspace(tmp_path / "temp", temp=True))
    await seed_thread(catalog_db, "sub", workspace(tmp_path / "sub"), kind="sub_agent")
    await seed_thread(catalog_db, "deleted", workspace(tmp_path / "deleted"), deleted=True)
    async with catalog_db() as session:
        session.add(BackgroundTaskRecord(task_id="shadow", thread_id="old", scope_key="shadow",
                    execution_backend="local", execution_locator=str(tmp_path / "shadow")))
        await session.commit()
    await service.import_existing_projects()
    projects = (await service.list_projects())["projects"]
    assert len(projects) == 1
    project = projects[0]
    assert project["key"] == "github:42"
    assert project["workspace"]["execution"]["locator"] == recent.locator
    await service.update_project(project["id"], {"label": "Keep me", "status": "archived", "is_pinned": True})
    await service.import_existing_projects()
    archived = (await service.list_projects(status="archived"))["projects"][0]
    assert archived["label"] == "Keep me" and archived["is_pinned"] is True
    assert len((await service.list_projects(status="archived", search="team/repo"))["projects"]) == 1
    assert len((await service.list_projects(status="archived", search=recent.locator))["projects"]) == 1
    assert (await navigation.list_conversation_projects())["projects"][0]["key"] == "temp"
    assert len((await navigation.list_project_threads(project_key="github:42"))["threads"]) == 2


@pytest.mark.asyncio
async def test_automatic_registration_excludes_temporary_and_child_threads(catalog_db, tmp_path):
    repository = workspace_repository.ThreadWorkspaceRepository()
    await seed_thread(catalog_db, "child", workspace(tmp_path / "child"), kind="sub_agent")
    await repository.upsert(thread_id="child", workspace=workspace(tmp_path / "child"))
    await repository.upsert(thread_id="root:sub:worker", workspace=workspace(tmp_path / "sub"))
    await repository.upsert(thread_id="temporary", workspace=workspace(tmp_path / "temp", source=" TEMP "))
    assert (await service.list_projects())["projects"] == []
    ref = workspace(tmp_path / "normal")
    await repository.upsert(thread_id="root", workspace=ref)
    project = (await service.list_projects())["projects"][0]
    await service.update_project(project["id"], {"status": "archived"})
    await repository.upsert(thread_id="another-root", workspace=ref)
    assert (await service.list_projects())["projects"] == []
    assert len((await service.list_projects(status="archived"))["projects"]) == 1
    with pytest.raises(ValueError, match="Temporary"):
        await service.add_project(workspace(tmp_path / "temporary", temp=True))


@pytest.mark.asyncio
async def test_backfill_imports_queued_tasks_without_conversations(catalog_db, tmp_path):
    ref = workspace(tmp_path / "queued")
    async with catalog_db() as session:
        for thread_id, metadata in (("queued", {}), ("temporary", {"temp": True}), ("parent:sub:child", {})):
            session.add(BackgroundTaskRecord(
                task_id=thread_id, thread_id=thread_id, execution_backend="local",
                execution_locator=ref.locator, execution_metadata_json=json.dumps(metadata),
            ))
        await session.commit()
    await service.import_existing_projects()
    projects = (await service.list_projects())["projects"]
    assert len(projects) == 1
    assert projects[0]["key"] == bind_workspace_ref(ref).scope.key
    assert (await navigation.list_conversation_projects())["projects"][0]["threads"] == []


@pytest.mark.asyncio
async def test_concurrent_registration_keeps_single_project(catalog_db, tmp_path):
    binding = bind_workspace_ref(workspace(tmp_path / "shared"))

    async def add():
        async with catalog_db() as session:
            await register_project(session, binding)
            await session.commit()

    await asyncio.gather(*(add() for _ in range(8)))
    assert len((await service.list_projects())["projects"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["daytona", "modal"])
async def test_catalog_keeps_repositories_in_separate_sandbox_roots(catalog_db, backend):
    refs = [resolve_workspace_ref({
        "backend": backend, "locator": "shared-sandbox",
        "metadata": {"root": f"/workspace/{name}", "source": "github",
                     "repository_id": index, "repository_full_name": f"team/{name}"},
    }) for index, name in enumerate(("first", "second"), start=1)]

    first, second = [await service.add_project(ref) for ref in refs]

    assert first["id"] != second["id"]
    assert first["key"] == "github:1" and second["key"] == "github:2"
    assert first["workspace"]["execution"]["metadata"]["root"] == "/workspace/first"
    assert second["workspace"]["execution"]["metadata"]["root"] == "/workspace/second"
    assert (await service.add_project(refs[0]))["id"] == first["id"]
    assert (await service.add_project(refs[1]))["id"] == second["id"]
    assert len((await service.list_projects())["projects"]) == 2


@pytest.mark.asyncio
async def test_background_saves_register_projects_and_prefer_thread_mapping(catalog_db, tmp_path):
    mapped = workspace(tmp_path / "mapped")
    task_workspace = workspace(tmp_path / "task-workspace")
    await seed_thread(catalog_db, "mapped-thread", mapped, kind="background")
    repository = task_repository.BackgroundTaskRepository()

    async def save(thread_id):
        await repository.upsert(
            task_id=f"task-{thread_id}", thread_id=thread_id, request="Work", agent_name="default",
            working_dir=None, workspace=task_workspace, notify_platform="", notify_external_id="",
            notify_channel_id="", status="running", result="", error="", created_at=1,
            started_at=None, completed_at=None,
        )

    await save("mapped-thread")
    projects = (await service.list_projects())["projects"]
    assert len(projects) == 1 and projects[0]["workspace"]["execution"]["locator"] == mapped.locator
    await save("task-only-thread")
    assert len((await service.list_projects())["projects"]) == 2


@pytest.mark.asyncio
async def test_pinned_cursor_pagination_and_literal_search(catalog_db, tmp_path):
    projects = [await service.add_project(workspace(tmp_path / value), label=value) for value in ["a", "b", "z", "100%_done"]]
    await service.update_project(projects[2]["id"], {"is_pinned": True})
    seen = []
    cursor = None
    while True:
        page = await navigation.list_conversation_projects(limit=1, after=cursor)
        seen += [project["key"] for project in page["projects"]]
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert seen[0] == projects[2]["key"]
    assert len(seen) == len(set(seen)) == 4
    found = (await service.list_projects(search="%_"))["projects"]
    assert [row["label"] for row in found] == ["100%_done"]
    assert (await service.list_projects(search="missing"))["projects"] == []
    with pytest.raises(ValueError, match="cursor"):
        await service.list_projects(after='[1,"x"]')


def test_project_cursor_compiles_for_postgres():
    statement = select(Project).where(cursor_condition(Project.is_pinned, Project.key.collate("C"), '[true,"github:42"]'))
    assert 'COLLATE "C"' in str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_catalog_navigation_and_search_queries_compile_for_postgres(monkeypatch):
    session = MagicMock()
    session.__aenter__.return_value = session
    session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    result.scalars.return_value = []
    session.execute = AsyncMock(return_value=result)
    for module in (navigation, service):
        monkeypatch.setattr(module, "get_async_session", AsyncMock(return_value=session))
    await navigation.list_conversation_projects(after='[true,"github:42"]')
    nav_sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "UNION ALL" in nav_sql and "is_pinned DESC" in nav_sql and "->>" in nav_sql
    await service.list_projects(search="C:\\source")
    search_sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert " AS JSON)" in search_sql and "->>" in search_sql


@pytest.mark.asyncio
async def test_project_api_round_trip_uses_persistent_catalog(catalog_db, tmp_path):
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    app.include_router(routes.router)
    ref = workspace(tmp_path / "api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/dashboard-api/projects", json={"workspace": ref.model_dump(), "label": "API project"})
        assert response.status_code == 200
        created = response.json()
        project_url = f"/dashboard-api/projects/{created['id']}"
        assert (await client.patch(project_url, json={"is_pinned": True})).json()["is_pinned"] is True
        assert (await client.delete(project_url)).json()["status"] == "removed"
        assert (await client.get("/dashboard-api/projects")).json()["projects"] == []
        assert len((await client.get("/dashboard-api/projects?status=removed")).json()["projects"]) == 1
        restored = (await client.post("/dashboard-api/projects", json={"workspace": created["workspace"]})).json()
        assert restored["id"] == created["id"] and restored["label"] == "API project"


def test_project_api_validation_and_errors(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    read = AsyncMock(return_value={"projects": [], "has_more": False, "next_cursor": None})
    monkeypatch.setattr(routes, "list_projects", read)
    assert client.get("/dashboard-api/projects?status=removed&search=hello").status_code == 200
    read.assert_awaited_once_with(status="removed", search="hello", limit=20, after=None)
    for path in ("?status=bad", "?limit=0", "?limit=101"):
        assert client.get(f"/dashboard-api/projects{path}").status_code == 422
    for body in ({"label": "   "}, {"label": None}, {"status": "removed"}, {"unknown": True}):
        assert client.patch("/dashboard-api/projects/1", json=body).status_code == 422
    assert client.post("/dashboard-api/projects", json={"workspace": {"locator": " "}}).status_code == 422
    create = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(routes, "add_project", create)
    assert client.post("/dashboard-api/projects", json={"workspace": {"locator": "/workspace"}, "label": " My project "}).status_code == 200
    assert create.await_args.kwargs["label"] == "My project"
    create.side_effect = ValueError("Temporary workspaces cannot be added")
    assert client.post("/dashboard-api/projects", json={"workspace": {"locator": "/temp"}}).status_code == 400
    write = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(routes, "update_project", write)
    assert client.patch("/dashboard-api/projects/1", json={"label": "  Trimmed  "}).status_code == 200
    write.assert_awaited_once_with(1, {"label": "Trimmed"})
    assert client.delete("/dashboard-api/projects/1").status_code == 200
    write.assert_awaited_with(1, {"status": "removed"})
    write.side_effect = KeyError("missing")
    assert client.patch("/dashboard-api/projects/99", json={"status": "active"}).status_code == 404
    read.side_effect = RuntimeError("Database offline")
    assert client.get("/dashboard-api/projects").status_code == 503


def test_runtime_switch_api_accepts_background_operation_and_reports_conflicts(monkeypatch, isolated_container):
    from agent.modules.projects import ProjectWorkspaceError
    from agent.modules.projects.workspaces import get_project_workspace_service
    projects = get_project_workspace_service(isolated_container)
    request = AsyncMock(return_value={"operation_id": "switch", "transition": {"phase": "saving"}})
    monkeypatch.setattr(projects.switcher, "request", request)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        url = "/dashboard-api/projects/7/runtime/switch"
        body = {"backend": "daytona", "expected_generation": 3, "project_workspace_id": 11}
        response = client.post(url, json=body)
        assert response.status_code == 202 and response.json()["operation_id"] == "switch"
        request.assert_awaited_once_with(7, "daytona", 3, 11)
        for invalid in ({**body, "backend": "unknown"}, {**body, "expected_generation": 0}, {"backend": "local"}, {**body, "force": True}):
            assert client.post(url, json=invalid).status_code == 422
        for code in ("workspace_busy", "workspace_switching", "stale_runtime"):
            request.side_effect = ProjectWorkspaceError("Workspace cannot switch", code)
            response = client.post(url, json=body)
            assert response.status_code == 409 and response.json()["detail"]["code"] == code


def test_local_sync_api_previews_by_default_and_rejects_arbitrary_target_paths(monkeypatch, isolated_container):
    from agent.modules.projects import ProjectWorkspaceError, get_project_workspace_service
    projects = get_project_workspace_service(isolated_container)
    synchronize = AsyncMock(return_value={"applied": False, "preview_token": "a" * 64, "conflicts": []})
    monkeypatch.setattr(projects.local_sync, "synchronize", synchronize)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        url = "/dashboard-api/projects/7/local-sync"
        body = {"expected_generation": 3, "project_workspace_id": 11}
        assert client.post(url, json=body).json()["applied"] is False
        synchronize.assert_awaited_once_with(7, 3, 11, preview_token=None)
        assert client.post(url, json={**body, "preview_token": "a" * 64}).status_code == 200
        synchronize.assert_awaited_with(7, 3, 11, preview_token="a" * 64)
        for invalid in ({**body, "path": "/outside"}, {**body, "force": True}, {**body, "preview_token": "bad"}, {"expected_generation": 0}):
            assert client.post(url, json=invalid).status_code == 422
        synchronize.side_effect = ProjectWorkspaceError("Local files changed", "local_sync_conflict")
        assert client.post(url, json=body).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("known_repository", [False, True])
async def test_github_resolution_ignores_registered_default_local_path(monkeypatch, isolated_container, tmp_path, known_repository):
    from agent.delivery.http.dashboard.routes import workspace as workspace_routes
    from agent.modules.projects import get_project_workspace_service
    projects = get_project_workspace_service(isolated_container)
    isolated_container._persistence_ready = True
    default = workspace(tmp_path / "default")
    unrelated = bind_workspace_ref(default, project_id=1, project_workspace_id=1, runtime_generation=1)
    selected = workspace(tmp_path / "repository", source="github", repository_id=123, repository_full_name="owner/repo")
    binding = bind_workspace_ref(selected, project_id=2, project_workspace_id=2, runtime_generation=1)
    find_binding = AsyncMock(return_value=unrelated)
    find_repository = AsyncMock(return_value=binding if known_repository else None)
    ensure = AsyncMock(return_value=binding)
    github = SimpleNamespace(resolve_repository_workspace=AsyncMock(return_value={"workspace": selected.model_dump()}))
    monkeypatch.setattr(projects, "find_binding", find_binding)
    monkeypatch.setattr(projects, "find_repository_binding", find_repository)
    monkeypatch.setattr(projects, "ensure", ensure)
    monkeypatch.setattr(workspace_routes, "get_github_automation_service", lambda request: github)
    result = await workspace_routes.resolve_dashboard_workspace(None, workspace_routes.WorkspaceResolveBody(
        kind="github", backend="local", repository_id=123, workspace=default, locator=default.locator,
    ))
    find_binding.assert_not_awaited()
    find_repository.assert_awaited_once_with(123)
    assert result["workspace"]["execution"]["locator"] == selected.locator
    if known_repository:
        assert result["reused_project"] is True
        assert result["workspace"]["project_id"] == 2
        ensure.assert_awaited_once_with(2, 2)
        github.resolve_repository_workspace.assert_not_awaited()
    else:
        ensure.assert_not_awaited()
        github.resolve_repository_workspace.assert_awaited_once_with(123)


def test_runtime_switch_migration_adds_transition_without_changing_existing_runtime(tmp_path):
    from sqlalchemy import create_engine, text
    from agent.modules.workspaces.migrations import migrate_workspace_tables
    url = "sqlite:///" + (tmp_path / "legacy-runtime.db").as_posix()
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE project_workspaces (id INTEGER PRIMARY KEY, source_json TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO project_workspaces VALUES (1, :ref)"), {"ref": '{"backend":"local","locator":"/original"}'})
        connection.execute(text("CREATE TABLE workspace_runtimes (id INTEGER PRIMARY KEY, execution_json TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO workspace_runtimes VALUES (1, :ref)"), {"ref": '{"backend":"local","locator":"/original"}'})
    migrate_workspace_tables(url)
    migrate_workspace_tables(url)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT execution_json, transition_json FROM workspace_runtimes WHERE id=1")).one()
        workspace = connection.execute(text("SELECT source_json, local_sync_revision FROM project_workspaces WHERE id=1")).one()
    assert workspace.local_sync_revision is None
    assert json.loads(workspace.source_json)["locator"] == "/original"
    assert json.loads(row.execution_json)["locator"] == "/original"
    assert json.loads(row.transition_json) is None
    engine.dispose()


def test_catalog_routes_require_dashboard_authentication(monkeypatch):
    from agent.delivery.http.dashboard.router import router
    from agent.modules.admin_auth import get_current_admin

    app = FastAPI()
    app.include_router(router)

    async def deny():
        raise HTTPException(status_code=401, detail="Authentication required")

    app.dependency_overrides[get_current_admin] = deny
    client = TestClient(app)
    for method, path in (("get", "/projects"), ("get", "/dashboard-api/projects"),
                         ("post", "/dashboard-api/projects"), ("patch", "/dashboard-api/projects/1"),
                         ("delete", "/dashboard-api/projects/1")):
        assert getattr(client, method)(path).status_code == 401


@pytest.mark.asyncio
async def test_project_list_batches_workspace_descriptions(catalog_db, tmp_path, monkeypatch):
    for index in range(5):
        await service.add_project(workspace(tmp_path / str(index)))
    sessions = AsyncMock(wraps=service.get_async_session)
    monkeypatch.setattr(service, "get_async_session", sessions)
    projects = (await service.list_projects())["projects"]
    assert len(projects) == 5 and all(len(project["workspaces"]) == 1 for project in projects)
    assert sessions.await_count == 2


@pytest.mark.asyncio
async def test_describe_missing_project_raises_not_found(catalog_db):
    from agent.modules.projects import get_project_workspace_service
    with pytest.raises(KeyError, match="Project not found"):
        await get_project_workspace_service().describe(999)


@pytest.mark.asyncio
async def test_add_project_rejects_a_different_execution_for_existing_scope(catalog_db, tmp_path):
    from httpx import ASGITransport, AsyncClient
    from agent.modules.projects import ProjectWorkspaceError
    original = bind_workspace_ref(workspace(tmp_path / "root"))
    project = await service.add_project(original)
    replacement = original.model_copy(update={"execution": original.execution.model_copy(update={"metadata": {"root": "different"}})})
    with pytest.raises(ProjectWorkspaceError, match="different workspace"):
        await service.add_project(replacement)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/dashboard-api/projects", json={"workspace": replacement.model_dump()})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workspace_conflict"
    current = await service.get_project(project["id"])
    assert current["workspace"] == project["workspace"]
