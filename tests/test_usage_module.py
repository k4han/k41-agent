from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.callbacks.manager import AsyncCallbackManager
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from starlette.requests import Request

from agent.delivery.http.dashboard.router import router as dashboard_router
from agent.modules.admin_auth import get_current_admin
from agent.modules.channels import ChannelManager
from agent.modules.usage import (
    LLMUsageRepository,
    LLMUsageCallback,
    UsageEventInput,
    UsageQuery,
    build_usage_context,
    extract_usage,
    usage_context_from_config,
    with_usage_tracking,
)
from agent.shared.infrastructure.db import Base, load_orm_models
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine


@pytest_asyncio.fixture
async def usage_db(monkeypatch: pytest.MonkeyPatch, tmp_path, request):
    await close_async_engine()

    db_path = tmp_path / f"{request.node.name}.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"

    import agent.shared.infrastructure.db.engine as engine_module

    monkeypatch.setattr(engine_module, "get_database_url", lambda: db_url)
    engine_module._cached_database_url = None

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)

    try:
        yield
    finally:
        await close_async_engine()


def test_extract_usage_from_ai_message_usage_metadata() -> None:
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="done",
                        usage_metadata={
                            "input_tokens": 12,
                            "output_tokens": 8,
                            "total_tokens": 20,
                            "input_token_details": {"cache_read": 3},
                            "output_token_details": {"reasoning": 2},
                        },
                    )
                )
            ]
        ]
    )

    usage = extract_usage(response)

    assert usage.has_usage_metadata is True
    assert usage.input_tokens == 12
    assert usage.output_tokens == 8
    assert usage.total_tokens == 20
    assert usage.input_token_details == {"cache_read": 3}
    assert usage.output_token_details == {"reasoning": 2}


def test_extract_usage_falls_back_to_llm_output_token_usage() -> None:
    response = LLMResult(
        generations=[[]],
        llm_output={
            "token_usage": {
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "total_tokens": 12,
            }
        },
    )

    usage = extract_usage(response)

    assert usage.has_usage_metadata is True
    assert usage.input_tokens == 5
    assert usage.output_tokens == 7
    assert usage.total_tokens == 12


def test_extract_usage_marks_missing_metadata() -> None:
    usage = extract_usage(LLMResult(generations=[[]]))

    assert usage.has_usage_metadata is False
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None


def test_with_usage_tracking_handles_async_callback_manager() -> None:
    manager = AsyncCallbackManager([])
    config = {
        "callbacks": manager,
        "configurable": {"thread_id": "telegram_123_456"},
    }

    updated = with_usage_tracking(
        config,
        agent_name="default",
        provider_name="openai-main",
        model_name="gpt-test",
    )

    callbacks = updated["callbacks"]
    assert isinstance(callbacks, AsyncCallbackManager)
    assert callbacks is not manager
    assert manager.handlers == []
    assert any(isinstance(handler, LLMUsageCallback) for handler in callbacks.handlers)


def test_build_usage_context_fills_missing_channel_from_thread_id() -> None:
    context = build_usage_context(
        "api_dashboard_91c23fc6b24a",
        {
            "platform": "api",
            "user_id": "dashboard",
            "channel_id": "",
        },
    )

    assert context.platform == "api"
    assert context.user_id == "dashboard"
    assert context.channel_id == "91c23fc6b24a"


def test_usage_context_from_config_fills_missing_channel_from_thread_id() -> None:
    context = usage_context_from_config(
        {
            "configurable": {"thread_id": "api_dashboard_91c23fc6b24a"},
            "metadata": {
                "usage_context": {
                    "platform": "api",
                    "user_id": "dashboard",
                    "channel_id": "",
                }
            },
        }
    )

    assert context.platform == "api"
    assert context.user_id == "dashboard"
    assert context.channel_id == "91c23fc6b24a"


@pytest.mark.asyncio
async def test_usage_repository_aggregates_by_user_channel(usage_db) -> None:
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)

    await repository.record(
        UsageEventInput(
            thread_id="telegram_123_456",
            root_thread_id="telegram_123_456",
            platform="telegram",
            user_id="123",
            channel_id="456",
            agent_name="default",
            provider_name="openai-main",
            model_name="gpt-test",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            created_at=now,
        )
    )
    await repository.record(
        UsageEventInput(
            thread_id="telegram_123_456:sub:worker:abc",
            root_thread_id="telegram_123_456",
            platform="telegram",
            user_id="123",
            channel_id="456",
            agent_name="worker",
            provider_name="openai-main",
            model_name="gpt-test",
            call_kind="agent",
            internal=False,
            has_usage_metadata=False,
            created_at=now,
        )
    )
    await repository.record(
        UsageEventInput(
            thread_id="discord_999",
            root_thread_id="discord_999",
            platform="discord",
            user_id="999",
            channel_id="",
            agent_name="default",
            provider_name="anthropic-main",
            model_name="claude-test",
            call_kind="router",
            internal=True,
            has_usage_metadata=True,
            input_tokens=3,
            output_tokens=4,
            total_tokens=7,
            created_at=now,
        )
    )

    query = UsageQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1))
    summary = await repository.summary(query)
    rows, total = await repository.grouped_by_identity(query)
    options = await repository.filter_options(query)

    assert total == 2
    assert summary == {
        "event_count": 3,
        "input_tokens": 13,
        "output_tokens": 24,
        "total_tokens": 37,
        "missing_usage_count": 1,
        "known_usage_count": 2,
        "internal_event_count": 1,
    }
    assert rows[0]["platform"] == "telegram"
    assert rows[0]["event_count"] == 2
    assert rows[0]["total_tokens"] == 30
    assert rows[0]["missing_usage_count"] == 1
    assert "openai-main" in options["providers"]
    assert {"platform": "telegram", "user_id": "123"} in options["users"]


@pytest.mark.asyncio
async def test_usage_repository_prunes_old_events(usage_db) -> None:
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)

    for created_at in (now - timedelta(days=100), now):
        await repository.record(
            UsageEventInput(
                thread_id="api_dashboard_thread",
                root_thread_id="api_dashboard_thread",
                platform="api",
                user_id="dashboard",
                channel_id="thread",
                agent_name="default",
                provider_name="openai-main",
                model_name="gpt-test",
                call_kind="agent",
                internal=False,
                has_usage_metadata=True,
                total_tokens=1,
                created_at=created_at,
            )
        )

    deleted = await repository.prune_before(now - timedelta(days=90))
    rows, total = await repository.grouped_by_identity(
        UsageQuery(start=now - timedelta(days=200), end=now + timedelta(days=1))
    )

    assert deleted == 1
    assert total == 1
    assert rows[0]["total_tokens"] == 1


def test_dashboard_usage_endpoint_enriches_identity_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    route_module = __import__(
        "agent.delivery.http.dashboard.routes.usage",
        fromlist=["usage"],
    )
    captured: dict = {}

    class FakeUsageService:
        async def dashboard_payload(self, query):
            captured["query"] = query
            return {
                "summary": {
                    "event_count": 1,
                    "known_usage_count": 1,
                    "missing_usage_count": 0,
                    "internal_event_count": 0,
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                },
                "rows": [
                    {
                        "platform": "telegram",
                        "user_id": "123",
                        "channel_id": "456",
                        "event_count": 1,
                        "missing_usage_count": 0,
                        "internal_event_count": 0,
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "total_tokens": 30,
                        "last_used_at": None,
                    }
                ],
                "filters": {
                    "platforms": ["telegram"],
                    "users": [{"platform": "telegram", "user_id": "123"}],
                    "channels": [
                        {"platform": "telegram", "user_id": "123", "channel_id": "456"}
                    ],
                    "agents": ["default"],
                    "providers": ["openai-main"],
                    "models": ["gpt-test"],
                },
                "pagination": {
                    "limit": 50,
                    "offset": 0,
                    "total": 1,
                    "has_more": False,
                    "next_offset": None,
                },
                "range": {
                    "start": "2026-01-01T00:00:00+00:00",
                    "end": "2026-01-02T00:00:00+00:00",
                },
            }

    async def fake_paired_identities():
        return [
            {
                "id": 1,
                "user_id": 42,
                "platform": "telegram",
                "external_id": "123",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(route_module, "get_usage_service", lambda: FakeUsageService())
    monkeypatch.setattr(route_module, "_paired_identities", fake_paired_identities)

    app = FastAPI()
    app.state.channel_manager = ChannelManager()
    app.include_router(dashboard_router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    response = TestClient(app).get(
        "/dashboard-api/usage",
        params={
            "platform": "telegram",
            "user_id": "123",
            "channel_id": "456",
            "provider": "openai-main",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"][0]["identity_label"] == "User #42 · telegram:123 · channel 456"
    assert payload["filters"]["users"][0]["label"] == "User #42 · telegram:123"
    assert captured["query"].platform == "telegram"
    assert captured["query"].provider_name == "openai-main"


@pytest.mark.asyncio
async def test_usage_repository_aggregates_by_thread(usage_db) -> None:
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)

    # Record events for the same thread
    await repository.record(
        UsageEventInput(
            thread_id="test_thread_1",
            root_thread_id="test_thread_1",
            platform="telegram",
            user_id="123",
            channel_id="456",
            agent_name="default",
            provider_name="google",
            model_name="gemini-1.5-pro",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            created_at=now,
        )
    )
    # Record sub-thread event for the same root thread
    await repository.record(
        UsageEventInput(
            thread_id="test_thread_1:sub:worker",
            root_thread_id="test_thread_1",
            platform="telegram",
            user_id="123",
            channel_id="456",
            agent_name="worker",
            provider_name="google",
            model_name="gemini-1.5-flash",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=200,
            output_tokens=100,
            total_tokens=300,
            created_at=now,
        )
    )
    # Record another thread event (should be ignored)
    await repository.record(
        UsageEventInput(
            thread_id="test_thread_2",
            root_thread_id="test_thread_2",
            platform="telegram",
            user_id="123",
            channel_id="456",
            agent_name="default",
            provider_name="openai",
            model_name="gpt-4",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
            created_at=now,
        )
    )

    data = await repository.aggregate_by_thread("test_thread_1")
    assert data["thread_id"] == "test_thread_1"
    assert data["total_tokens"] == 450
    assert data["input_tokens"] == 300
    assert data["output_tokens"] == 150
    assert len(data["models"]) == 2

    # Check order (descending by total_tokens)
    assert data["models"][0]["model"] == "gemini-1.5-flash"
    assert data["models"][0]["total_tokens"] == 300
    assert data["models"][0]["percentage"] == 66.7

    assert data["models"][1]["model"] == "gemini-1.5-pro"
    assert data["models"][1]["total_tokens"] == 150
    assert data["models"][1]["percentage"] == 33.3


@pytest.mark.asyncio
async def test_usage_repository_aggregates_by_workspace(usage_db) -> None:
    from agent.modules.workspaces.models import ThreadWorkspace
    from agent.shared.infrastructure.db.session import get_async_session

    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)

    # Create workspace bindings for thread_1 and thread_2
    session = await get_async_session()
    async with session:
        session.add(
            ThreadWorkspace(
                thread_id="workspace_thread_1",
                workspace_backend="local",
                workspace_locator="/path/to/project_a",
                workspace_label="Project A",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ThreadWorkspace(
                thread_id="workspace_thread_2",
                workspace_backend="local",
                workspace_locator="/path/to/project_a",
                workspace_label="Project A",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ThreadWorkspace(
                thread_id="workspace_thread_3",
                workspace_backend="local",
                workspace_locator="/path/to/project_b",
                workspace_label="Project B",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    # Records for workspace_thread_1
    await repository.record(
        UsageEventInput(
            thread_id="workspace_thread_1",
            root_thread_id="workspace_thread_1",
            platform="api",
            user_id="user1",
            channel_id="chan1",
            agent_name="default",
            provider_name="google",
            model_name="gemini-1.5-pro",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            created_at=now,
        )
    )
    # Records for workspace_thread_2
    await repository.record(
        UsageEventInput(
            thread_id="workspace_thread_2",
            root_thread_id="workspace_thread_2",
            platform="api",
            user_id="user1",
            channel_id="chan1",
            agent_name="default",
            provider_name="google",
            model_name="gemini-1.5-pro",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=2000,
            output_tokens=1000,
            total_tokens=3000,
            created_at=now,
        )
    )
    # Records for workspace_thread_3 (Project B - should be ignored)
    await repository.record(
        UsageEventInput(
            thread_id="workspace_thread_3",
            root_thread_id="workspace_thread_3",
            platform="api",
            user_id="user1",
            channel_id="chan1",
            agent_name="default",
            provider_name="google",
            model_name="gemini-1.5-pro",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=5,
            output_tokens=5,
            total_tokens=10,
            created_at=now,
        )
    )

    data = await repository.aggregate_by_workspace("local", "/path/to/project_a")
    assert data["backend"] == "local"
    assert data["locator"] == "/path/to/project_a"
    assert data["total_tokens"] == 4500
    assert data["input_tokens"] == 3000
    assert data["output_tokens"] == 1500
    assert len(data["models"]) == 1
    assert data["models"][0]["model"] == "gemini-1.5-pro"
    assert data["models"][0]["calls"] == 2
