from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.delivery.http.dashboard.routes import dashboard, tasks
from agent.modules.agent_runtime.background_tasks import BackgroundTask, BackgroundTaskManager


@pytest.fixture
def manager():
    manager = BackgroundTaskManager()
    manager._tasks = {
        str(index): BackgroundTask(
            task_id=str(index), thread_id=f"thread-{index}", created_at=float(index),
        )
        for index in range(120)
    }
    return manager


def test_manager_serializes_only_requested_page(manager, monkeypatch):
    original = BackgroundTask.to_dict
    serialized = []

    def serialize(task):
        serialized.append(task.task_id)
        return original(task)

    monkeypatch.setattr(BackgroundTask, "to_dict", serialize)
    page = manager.list_all(limit=3, offset=55)
    assert [task["task_id"] for task in page] == ["64", "63", "62"]
    assert serialized == ["64", "63", "62"]
    assert manager.count() == 120
    assert len(manager.list_all()) == 50
    assert manager.list_all(offset=120) == []


@pytest.mark.parametrize("module,path", [
    (tasks, "/tasks/list"),
    (dashboard, "/dashboard-api/tasks"),
])
def test_task_routes_paginate_before_thread_lookup(module, path, manager, monkeypatch):
    repo = SimpleNamespace(list_active_thread_ids=AsyncMock(return_value={"thread-64"}))
    monkeypatch.setattr(module, "get_background_task_manager", lambda: manager)
    monkeypatch.setattr(module, "get_conversation_thread_repository", lambda: repo)
    monkeypatch.setattr(dashboard, "get_catalog_service", lambda: SimpleNamespace(list_agents=lambda: []))
    monkeypatch.setattr(dashboard, "paired_identities", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)

    response = client.get(path, params={"limit": 3, "offset": 55})
    assert response.status_code == 200
    payload = response.json()
    assert (payload["total"], payload["limit"], payload["offset"]) == (120, 3, 55)
    assert [task["task_id"] for task in payload["tasks"]] == ["64", "63", "62"]
    assert [task["thread_deleted"] for task in payload["tasks"]] == [False, True, True]
    repo.list_active_thread_ids.assert_awaited_once_with(["thread-64", "thread-63", "thread-62"])

    default = client.get(path).json()
    assert len(default["tasks"]) == default["limit"] == 50
    assert default["offset"] == 0
    assert client.get(path, params={"offset": 120}).json()["tasks"] == []
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "invalid"}):
        assert client.get(path, params=params).status_code == 422
