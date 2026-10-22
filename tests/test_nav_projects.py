from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.delivery.http.dashboard.routes import conversations
from agent.modules.agent_runtime.models import BackgroundTaskRecord
from agent.modules.conversations import navigation
from agent.modules.conversations.models import ConversationThread
from agent.modules.workspaces.models import ThreadWorkspace


@pytest_asyncio.fixture
async def project_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (ConversationThread, ThreadWorkspace, BackgroundTaskRecord):
            await connection.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def get_session():
        return maker()

    monkeypatch.setattr(navigation, "get_async_session", get_session)
    try:
        yield maker
    finally:
        await engine.dispose()


async def add_project(maker, key, count, *, metadata=None, task_only=False, kind="user", deleted=False):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with maker() as session:
        for index in range(count):
            thread_id = f"{key}-{index}"
            session.add(ConversationThread(
                thread_id=thread_id, platform="api", user_id="dashboard", title=thread_id,
                kind=kind, created_at=now, updated_at=now + timedelta(seconds=index),
                deleted_at=now if deleted else None,
            ))
            if key == "none":
                continue
            fields = dict(
                thread_id=thread_id, scope_key=key, scope_kind="github" if key.startswith("github:") else "local",
                scope_label=key, execution_backend="local", execution_locator=f"C:/work/{key}/{index}",
                execution_label=key, execution_metadata_json=json.dumps(metadata or {}),
            )
            if task_only:
                session.add(BackgroundTaskRecord(task_id=f"task-{thread_id}", **fields))
            else:
                session.add(ThreadWorkspace(**fields))
        await session.commit()


@pytest.mark.asyncio
async def test_projects_page_by_workspace_with_four_recent_previews(project_db, monkeypatch):
    await add_project(project_db, "a", 30)
    await add_project(project_db, "b", 2)
    await add_project(project_db, "c", 1)

    async def forbidden_stats(*args, **kwargs):
        pytest.fail("Navigation must never load checkpoint stats")

    monkeypatch.setattr(conversations, "list_user_threads_with_stats", forbidden_stats)
    first = await conversations.get_nav_projects(limit=2, after=None, active_thread_id="")
    assert [project["key"] for project in first["projects"]] == ["a", "b"]
    assert [row["thread_id"] for row in first["projects"][0]["threads"]] == ["a-29", "a-28", "a-27", "a-26"]
    assert len(first["projects"][1]["threads"]) == 2
    assert "checkpoint_count" not in first["projects"][0]["threads"][0]
    assert first["has_more"] is True
    assert first["next_cursor"] == "b"

    async with project_db() as session:
        await session.execute(update(ConversationThread).where(ConversationThread.thread_id == "c-0")
                              .values(updated_at=datetime(2027, 1, 1, tzinfo=timezone.utc)))
        await session.commit()
    second = await navigation.list_conversation_projects(limit=2, after=first["next_cursor"])
    assert [project["key"] for project in second["projects"]] == ["c"]
    assert second["has_more"] is False
    assert second["next_cursor"] is None


@pytest.mark.asyncio
async def test_active_old_conversation_stays_within_four_previews(project_db):
    await add_project(project_db, "a", 8)
    result = await navigation.list_conversation_projects(active_thread_id="a-0")
    assert [row["thread_id"] for row in result["projects"][0]["threads"]] == ["a-7", "a-6", "a-5", "a-0"]


@pytest.mark.asyncio
async def test_project_threads_paginate_independently_without_checkpoint_stats(project_db):
    await add_project(project_db, "a", 45)
    await add_project(project_db, "b", 3)
    pages = [await navigation.list_project_threads(project_key="a", limit=20, offset=offset)
             for offset in (0, 20, 40)]
    assert [len(page["threads"]) for page in pages] == [20, 20, 5]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert [page["next_offset"] for page in pages] == [20, 40, 45]
    rows = [row for page in pages for row in page["threads"]]
    assert [row["thread_id"] for row in rows] == [f"a-{index}" for index in reversed(range(45))]
    assert all(row["workspace_key"] == "a" and "checkpoint_count" not in row for row in rows)
    assert (await navigation.list_project_threads(project_key="missing"))["threads"] == []


@pytest.mark.asyncio
async def test_project_thread_paging_respects_temp_and_missing_workspace_groups(project_db):
    await add_project(project_db, "temp-one", 3, metadata={"temp": True})
    await add_project(project_db, "temp-two", 2, metadata={"source": " TEMP "})
    await add_project(project_db, "none", 2)
    await add_project(project_db, "deleted", 1, metadata={"temp": True}, deleted=True)
    temp = await navigation.list_project_threads(project_key="temp", limit=4)
    assert len(temp["threads"]) == 4
    assert temp["has_more"] is True
    assert all(row["workspace_key"] == "temp" for row in temp["threads"])
    missing = await navigation.list_project_threads(project_key="no-workspace")
    assert len(missing["threads"]) == 2
    assert all(row["workspace"] is None for row in missing["threads"])


@pytest.mark.asyncio
async def test_temp_missing_github_and_background_workspace_groups(project_db):
    await add_project(project_db, "temp-one", 3, metadata={"temp": True})
    await add_project(project_db, "temp-two", 3, metadata={"source": " TEMP "})
    await add_project(project_db, "none", 2)
    await add_project(project_db, "github:42", 1, metadata={"source": "github", "repository_id": 42, "repository_full_name": "team/repo"})
    await add_project(project_db, "task", 1, task_only=True, kind="background")
    await add_project(project_db, "deleted", 1, deleted=True)
    await add_project(project_db, "sub", 1, kind="sub_agent")
    result = await navigation.list_conversation_projects()
    groups = {project["key"]: project for project in result["projects"]}
    assert set(groups) == {"temp", "no-workspace", "github:42", "task"}
    assert groups["temp"]["label"] == "Temp workspace"
    assert len(groups["temp"]["threads"]) == 4
    assert groups["temp"]["threads"][0]["workspace"]["scope"]["key"].startswith("local:")
    assert groups["no-workspace"]["threads"][0]["workspace"] is None
    assert groups["github:42"]["label"] == "team/repo"
    assert groups["task"]["threads"][0]["workspace"]["execution"]["locator"].startswith("C:/work/task")


@pytest.mark.asyncio
async def test_thread_mapping_overrides_background_task_workspace(project_db):
    await add_project(project_db, "a", 1, task_only=True, kind="background")
    async with project_db() as session:
        session.add(ThreadWorkspace(thread_id="a-0", scope_key="override", execution_backend="local",
                                    execution_locator="C:/override", execution_label="Override", execution_metadata_json="{}"))
        await session.commit()
    result = await navigation.list_conversation_projects()
    assert result["projects"][0]["key"] == "override"
    assert result["projects"][0]["label"] == "Override"


def test_project_query_supports_postgres_json_and_window_functions():
    statement = navigation._project_previews_statement(dialect="postgresql", limit=20, after="a", active_thread_id="a-0")
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "row_number() OVER" in sql
    assert " AS JSON)" in sql
    assert "->>" in sql


def test_navigation_api_validates_parameters_and_reports_unavailability(monkeypatch):
    app = FastAPI()
    app.include_router(conversations.router)
    client = TestClient(app)
    read = AsyncMock(return_value={"projects": [], "has_more": False, "next_cursor": None})
    monkeypatch.setattr(conversations, "list_conversation_projects", read)
    response = client.get("/dashboard-api/nav-projects", params={"limit": 2, "after": "local:C:/work", "active_thread_id": "old"})
    assert response.status_code == 200
    read.assert_awaited_once_with(limit=2, after="local:C:/work", active_thread_id="old")
    assert client.get("/dashboard-api/nav-projects", params={"limit": 0}).status_code == 422
    assert client.get("/dashboard-api/nav-projects", params={"limit": 101}).status_code == 422
    read.side_effect = RuntimeError("Database unavailable")
    assert client.get("/dashboard-api/nav-projects").status_code == 503


def test_project_threads_api_validates_pagination_and_reports_unavailability(monkeypatch):
    app = FastAPI()
    app.include_router(conversations.router)
    client = TestClient(app)
    read = AsyncMock(return_value={"threads": [], "has_more": False, "next_offset": 20})
    monkeypatch.setattr(conversations, "list_project_threads", read)
    params = {"project_key": "local:C:/work & notes", "limit": 20, "offset": 20}
    assert client.get("/dashboard-api/nav-projects/threads", params=params).status_code == 200
    read.assert_awaited_once_with(**params)
    for invalid in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"project_key": ""}):
        assert client.get("/dashboard-api/nav-projects/threads", params={**params, **invalid}).status_code == 422
    read.side_effect = RuntimeError("Database unavailable")
    assert client.get("/dashboard-api/nav-projects/threads", params=params).status_code == 503


@pytest.mark.asyncio
async def test_new_chat_cannot_reuse_another_temporary_workspace(tmp_path, monkeypatch):
    router = importlib.import_module("agent.delivery.http.api.router")
    from agent.delivery.http.api.schemas import ChatRequest
    from agent.modules.workspaces import resolve_workspace_ref

    old = resolve_workspace_ref({"backend": "local", "locator": str(tmp_path / "old"), "metadata": {"temp": True}})
    new = resolve_workspace_ref({"backend": "local", "locator": str(tmp_path / "new"), "metadata": {"temp": True}})
    create = AsyncMock(return_value=new)
    remember = AsyncMock()
    monkeypatch.setattr(router, "create_temp_workspace", create)
    monkeypatch.setattr(router, "remember_thread_workspace_ref", remember)
    params = {"thread_id": "new"}
    await router._apply_workspace_to_run_params(ChatRequest(message="Hello", user_id="dashboard", workspace=old), params)
    create.assert_awaited_once_with("new")
    assert params["workspace"] == new
    remember.assert_awaited_once_with("new", new)

    create.reset_mock()
    await router._apply_workspace_to_run_params(ChatRequest(message="Continue", user_id="dashboard", thread_id="old", workspace=old), {"thread_id": "old"})
    create.assert_not_awaited()
