from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    UsageService,
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
async def usage_db(tmp_path, request):
    await close_async_engine()

    db_path = tmp_path / f"{request.node.name}.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"

    from agent.bootstrap.container import create_test_container, set_active_container

    set_active_container(create_test_container(database_url=db_url))

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
    assert "router" in options["call_kinds"]
    assert {"platform": "telegram", "user_id": "123"} in options["users"]

    internal_query = UsageQuery(
        start=now - timedelta(minutes=1),
        end=now + timedelta(minutes=1),
        call_kind="router",
        internal=True,
    )
    internal_summary = await repository.summary(internal_query)
    internal_rows, internal_total = await repository.grouped_by_identity(internal_query)

    assert internal_summary["event_count"] == 1
    assert internal_summary["total_tokens"] == 7
    assert internal_total == 1
    assert internal_rows[0]["platform"] == "discord"


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


@pytest.mark.asyncio
async def test_usage_service_dashboard_payload_loads_only_requested_view() -> None:
    now = datetime.now(timezone.utc)
    calls: list[str] = []

    class FakeRepository:
        async def prune_before(self, cutoff):
            calls.append("prune")
            return 0

        async def summary(self, query):
            calls.append("summary")
            return {
                "event_count": 1,
                "known_usage_count": 1,
                "missing_usage_count": 0,
                "internal_event_count": 0,
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
            }

        async def grouped_by_identity(self, query):
            raise AssertionError("user rows should not be loaded for workspace view")

        async def aggregate_workspaces(self, query):
            calls.append("workspaces")
            return [{"backend": "local", "locator": "/repo"}]

        async def aggregate_threads(self, query):
            raise AssertionError("threads should not be loaded for workspace view")

        async def filter_options(self, query):
            calls.append("filters")
            return {
                "platforms": [],
                "users": [],
                "channels": [],
                "agents": [],
                "providers": [],
                "models": [],
                "call_kinds": [],
            }

    payload = await UsageService(FakeRepository()).dashboard_payload(
        UsageQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1)),
        view="workspaces",
    )

    assert calls == ["prune", "summary", "workspaces", "filters"]
    assert payload["view"] == "workspaces"
    assert payload["rows"] == []
    assert payload["workspaces"] == [{"backend": "local", "locator": "/repo"}]
    assert payload["threads"] == []
    assert payload["pagination"]["total"] == 0


@pytest.mark.asyncio
async def test_usage_service_dashboard_payload_uses_configured_display_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.usage.service as service_module

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class FakeRepository:
        async def prune_before(self, cutoff):
            return 0

        async def summary(self, query):
            return {
                "event_count": 1,
                "known_usage_count": 1,
                "missing_usage_count": 0,
                "internal_event_count": 0,
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
            }

        async def grouped_by_identity(self, query):
            return [
                {
                    "platform": "api",
                    "user_id": "dashboard",
                    "channel_id": "thread",
                    "event_count": 1,
                    "missing_usage_count": 0,
                    "internal_event_count": 0,
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "last_used_at": "2026-01-01T00:00:00+00:00",
                }
            ], 1

        async def aggregate_workspaces(self, query):
            return [
                {
                    "backend": "local",
                    "locator": "/repo",
                    "last_used_at": "2026-01-01T00:00:00+00:00",
                }
            ]

        async def aggregate_threads(self, query):
            return [
                {
                    "thread_id": "thread",
                    "last_used_at": "2026-01-01T00:00:00+00:00",
                }
            ]

        async def filter_options(self, query):
            return {
                "platforms": [],
                "users": [],
                "channels": [],
                "agents": [],
                "providers": [],
                "models": [],
                "call_kinds": [],
            }

    monkeypatch.setattr(
        service_module,
        "resolve_display_timezone",
        lambda: ("Asia/Bangkok", ZoneInfo("Asia/Bangkok")),
    )

    payload = await UsageService(FakeRepository()).dashboard_payload(
        UsageQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1)),
        view="all",
    )

    assert payload["display_timezone"] == "Asia/Bangkok"
    assert payload["rows"][0]["last_used_at"] == "2026-01-01T07:00:00+07:00"
    assert payload["workspaces"][0]["last_used_at"] == "2026-01-01T07:00:00+07:00"
    assert payload["threads"][0]["last_used_at"] == "2026-01-01T07:00:00+07:00"


def test_dashboard_usage_endpoint_enriches_identity_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    route_module = __import__(
        "agent.delivery.http.dashboard.routes.usage",
        fromlist=["usage"],
    )
    captured: dict = {}

    class FakeUsageService:
        async def dashboard_payload(self, query, view="all"):
            captured["query"] = query
            captured["view"] = view
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
                    "call_kinds": ["agent"],
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
    monkeypatch.setattr(route_module, "paired_identities", fake_paired_identities)
    monkeypatch.setattr(
        route_module,
        "resolve_display_timezone",
        lambda: ("Asia/Bangkok", ZoneInfo("Asia/Bangkok")),
    )

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
            "call_kind": "agent",
            "internal": "false",
            "view": "users",
            "start": "2026-01-01",
            "end": "2026-01-01",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"][0]["identity_label"] == "User #42 · telegram:123 · channel 456"
    assert payload["filters"]["users"][0]["label"] == "User #42 · telegram:123"
    assert captured["query"].platform == "telegram"
    assert captured["query"].provider_name == "openai-main"
    assert captured["query"].call_kind == "agent"
    assert captured["query"].internal is False
    assert captured["view"] == "users"
    assert captured["query"].start.isoformat() == "2025-12-31T17:00:00+00:00"
    assert captured["query"].end.isoformat() == "2026-01-01T16:59:59.999000+00:00"


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
async def test_usage_repository_thread_current_context_includes_latest_external_response(usage_db) -> None:
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)

    await repository.record(
        UsageEventInput(
            thread_id="context_thread",
            root_thread_id="context_thread",
            platform="dashboard",
            user_id="admin",
            channel_id="admin",
            agent_name="default",
            provider_name="test",
            model_name="test-model",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=60_000,
            output_tokens=500,
            total_tokens=60_500,
            created_at=now,
        )
    )
    await repository.record(
        UsageEventInput(
            thread_id="context_thread",
            root_thread_id="context_thread",
            platform="dashboard",
            user_id="admin",
            channel_id="admin",
            agent_name="default",
            provider_name="test",
            model_name="test-model",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=88_000,
            output_tokens=700,
            total_tokens=88_700,
            usage_metadata={"context_breakdown": {
                "system_prompt": 10_000, "system_tools": 8_000, "skills": 5_000,
                "subagents": 5_000, "user_messages": 20_000,
                "agent_responses": 20_000, "tool_calls": 20_000,
            }},
            created_at=now + timedelta(seconds=1),
        )
    )
    await repository.record(
        UsageEventInput(
            thread_id="context_thread:sub:worker",
            root_thread_id="context_thread",
            platform="dashboard",
            user_id="admin",
            channel_id="admin",
            agent_name="worker",
            provider_name="test",
            model_name="worker-model",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=120_000,
            output_tokens=100,
            total_tokens=120_100,
            created_at=now + timedelta(seconds=2),
        )
    )
    await repository.record(
        UsageEventInput(
            thread_id="context_thread",
            root_thread_id="context_thread",
            platform="dashboard",
            user_id="admin",
            channel_id="admin",
            agent_name="conversation-title",
            provider_name="test",
            model_name="title-model",
            call_kind="conversation_title",
            internal=True,
            has_usage_metadata=True,
            input_tokens=5,
            output_tokens=5,
            total_tokens=10,
            created_at=now + timedelta(seconds=3),
        )
    )

    data = await repository.aggregate_by_thread("context_thread")

    assert data["input_tokens"] == 268_005
    assert data["current_context_tokens"] == 88_700
    assert data["has_context_usage"] is True
    assert data["latest_input_tokens"] == 88_000
    assert data["context_breakdown"]["skills"] == 5_000
    assert data["context_breakdown"]["agent_responses"] == 20_700
    assert sum(data["context_breakdown"].values()) == 88_700
    assert data["latest_output_tokens"] == 700
    assert data["latest_total_tokens"] == 88_700
    assert data["latest_model"] == "test-model"
    assert data["latest_provider"] == "test"
    assert data["latest_used_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_calls", [[], [{
    "name": "read", "args": {"path": "source.py"}, "id": "read-1",
}]])
async def test_first_response_context_matches_stream_after_reload(usage_db, monkeypatch, tool_calls):
    from uuid import uuid4

    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.modules.usage import tracking
    from agent.modules.usage.context_breakdown import estimate_context_breakdown
    from agent.modules.workflows import model_context

    records, events = [], []
    monkeypatch.setattr(tracking, "_schedule_record", records.append)
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    estimates = estimate_context_breakdown([
        SystemMessage(content="Instructions"), HumanMessage(content="Hello"),
    ])
    config = with_usage_tracking(
        {"configurable": {"thread_id": "first_response"}}, agent_name="default",
        provider_name="test", model_name="test-model", context_breakdown=estimates,
    )
    response = AIMessage(content="First answer", tool_calls=tool_calls, usage_metadata={
        "input_tokens": 123, "output_tokens": 80, "total_tokens": 203,
    })
    config["callbacks"][-1].on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=response)]]), run_id=uuid4(),
    )
    model_context.emit_reported_context_usage(
        response, resolved=SimpleNamespace(provider_name="test", model_name="test-model"),
        config=config, context_breakdown=estimates,
    )
    repository = LLMUsageRepository()
    await repository.record(records[0])
    payload = await repository.aggregate_by_thread("first_response")
    assert payload["current_context_tokens"] == events[0]["current_context_tokens"] == 203
    assert payload["context_breakdown"] == events[0]["context_breakdown"]
    assert payload["context_breakdown"]["agent_responses"] > 0
    assert (payload["context_breakdown"]["tool_calls"] > 0) == bool(tool_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_usage_metadata", [True, False])
async def test_usage_repository_preserves_context_after_summary_without_applied_compaction(
    usage_db, has_usage_metadata,
):
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)
    identity = {
        "thread_id": "compaction_thread", "root_thread_id": "compaction_thread",
        "platform": "dashboard", "user_id": "admin", "channel_id": "admin",
        "provider_name": "test", "model_name": "test-model",
    }
    await repository.record(UsageEventInput(
        **identity, agent_name="default", call_kind="agent", internal=False,
        has_usage_metadata=True, input_tokens=8000, output_tokens=100,
        total_tokens=8100, created_at=now,
    ))
    await repository.record(UsageEventInput(
        **identity, agent_name="conversation-compactor", call_kind="compaction",
        internal=True, has_usage_metadata=has_usage_metadata,
        input_tokens=3000 if has_usage_metadata else None,
        output_tokens=100 if has_usage_metadata else None,
        total_tokens=3100 if has_usage_metadata else None,
        usage_metadata={"input_tokens": 3000} if has_usage_metadata else None,
        created_at=now + timedelta(seconds=1),
    ))

    payload = await repository.aggregate_by_thread("compaction_thread")
    assert payload["has_context_usage"] is True
    assert payload["current_context_tokens"] == 8100


@pytest.mark.asyncio
@pytest.mark.parametrize("has_provider_report", [True, False])
@pytest.mark.parametrize("retained_tokens", [0, 2000])
async def test_usage_repository_retains_compacted_estimate_until_next_model_call(
    usage_db, has_provider_report, retained_tokens,
):
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)
    identity = {
        "thread_id": "compaction_thread", "root_thread_id": "compaction_thread",
        "platform": "dashboard", "user_id": "admin", "channel_id": "admin",
        "provider_name": "test", "model_name": "test-model",
    }
    if has_provider_report:
        await repository.record(UsageEventInput(
            **identity, agent_name="default", call_kind="agent", internal=False,
            has_usage_metadata=True, input_tokens=8000, output_tokens=100,
            total_tokens=8100, created_at=now,
        ))
    await repository.record(UsageEventInput(
        **identity, agent_name="conversation-compactor", call_kind="compaction",
        internal=True, has_usage_metadata=False, input_tokens=0, output_tokens=0,
        total_tokens=0, usage_metadata={"retained_tokens": retained_tokens},
        created_at=now + timedelta(seconds=1),
    ))
    # A later summarizer call does not replace the last applied compaction marker.
    await repository.record(UsageEventInput(
        **identity, agent_name="conversation-compactor", call_kind="compaction",
        internal=True, has_usage_metadata=False,
        created_at=now + timedelta(seconds=2),
    ))

    payload = await repository.aggregate_by_thread("compaction_thread")
    assert payload["has_context_usage"] is False
    assert payload["context_estimated"] is True
    assert payload["current_context_tokens"] == retained_tokens
    assert payload["latest_input_tokens"] == 0
    assert payload["latest_output_tokens"] == 0
    assert payload["context_breakdown"] is None

    await repository.record(UsageEventInput(
        **identity, agent_name="default", call_kind="agent", internal=False,
        has_usage_metadata=True, input_tokens=2500, output_tokens=100,
        total_tokens=2600, created_at=now + timedelta(seconds=3),
    ))
    payload = await repository.aggregate_by_thread("compaction_thread")
    assert payload["has_context_usage"] is True
    assert payload["context_estimated"] is False
    assert payload["current_context_tokens"] == 2600


@pytest.mark.asyncio
async def test_usage_service_returns_provider_tokens_without_loading_checkpoint_estimates(monkeypatch):
    repository = SimpleNamespace(aggregate_by_thread=AsyncMock(return_value={
        "thread_id": "context_thread", "current_context_tokens": 88000,
        "latest_input_tokens": 88000, "has_context_usage": True,
    }))
    checkpointer = SimpleNamespace(aget_tuple=AsyncMock(side_effect=AssertionError("Checkpoint estimates must not be loaded")))
    monkeypatch.setattr("agent.modules.conversations.get_history_checkpointer", lambda: checkpointer)
    payload = await UsageService(repository).get_thread_usage("context_thread")
    assert payload["current_context_tokens"] == 88000
    assert payload["latest_input_tokens"] == 88000
    assert "estimated_context_tokens" not in payload
    checkpointer.aget_tuple.assert_not_called()


@pytest.mark.asyncio
async def test_usage_repository_restores_compacted_breakdown_without_stale_history(usage_db):
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)
    identity = {
        "thread_id": "snapshot_thread", "root_thread_id": "snapshot_thread",
        "platform": "dashboard", "user_id": "admin", "channel_id": "admin",
        "provider_name": "test", "model_name": "test-model",
    }
    await repository.record(UsageEventInput(
        **identity, agent_name="default", call_kind="agent", internal=False,
        has_usage_metadata=True, input_tokens=80000, output_tokens=1000,
        total_tokens=81000, created_at=now,
    ))
    breakdown = {
        "system_prompt": 1000, "system_tools": 800, "skills": 500, "subagents": 300,
        "user_messages": 200, "agent_responses": 100, "tool_calls": 50,
    }
    await repository.record(UsageEventInput(
        **identity, agent_name="conversation-compactor", call_kind="compaction",
        internal=True, has_usage_metadata=False, input_tokens=0, output_tokens=0,
        total_tokens=0, usage_metadata={"retained_tokens": 350, "context_breakdown": breakdown},
        created_at=now + timedelta(seconds=1),
    ))
    payload = await repository.aggregate_by_thread("snapshot_thread")
    assert payload["context_breakdown"] == breakdown
    assert payload["current_context_tokens"] == sum(breakdown.values())
    assert payload["context_estimated"] is True
    assert payload["has_context_usage"] is False
    assert payload["total_tokens"] == 81000


@pytest.mark.asyncio
async def test_usage_service_recovers_breakdown_for_older_compaction_markers(usage_db, monkeypatch):
    from langchain_core.messages import HumanMessage
    from agent.modules.usage.context_breakdown import estimate_compacted_context_breakdown

    messages = [HumanMessage(content="Retained summary"), AIMessage(content="Acknowledged")]
    previous = {
        "system_prompt": 1000, "system_tools": 800, "skills": 500, "subagents": 300,
        "user_messages": 40000, "agent_responses": 30000, "tool_calls": 20000,
    }
    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)
    identity = {
        "thread_id": "legacy_thread", "root_thread_id": "legacy_thread",
        "platform": "dashboard", "user_id": "admin", "channel_id": "admin",
        "provider_name": "test", "model_name": "test-model",
    }
    await repository.record(UsageEventInput(
        **identity, agent_name="default", call_kind="agent", internal=False,
        has_usage_metadata=True, input_tokens=sum(previous.values()), output_tokens=100,
        total_tokens=sum(previous.values()) + 100, usage_metadata={"context_breakdown": previous},
        created_at=now,
    ))
    await repository.record(UsageEventInput(
        **identity, agent_name="conversation-compactor", call_kind="compaction",
        internal=True, has_usage_metadata=False, input_tokens=0, output_tokens=0,
        total_tokens=0, usage_metadata={"retained_tokens": 200},
        created_at=now + timedelta(seconds=1),
    ))
    await repository.record(UsageEventInput(
        **identity, agent_name="conversation-compactor", call_kind="compaction",
        internal=True, has_usage_metadata=True, input_tokens=3000, output_tokens=100,
        total_tokens=3100, usage_metadata={"context_breakdown": {**previous, "skills": 99999}},
        created_at=now + timedelta(seconds=2),
    ))
    checkpointer = SimpleNamespace(aget_tuple=AsyncMock(return_value=SimpleNamespace(
        checkpoint={"channel_values": {"messages": messages}},
    )))
    monkeypatch.setattr("agent.modules.conversations.history.get_history_checkpointer", lambda: checkpointer)
    payload = await UsageService(repository).get_thread_usage("legacy_thread")
    expected = estimate_compacted_context_breakdown(messages, previous)
    assert payload["context_breakdown"] == expected
    assert payload["current_context_tokens"] == sum(expected.values())
    assert payload["context_estimated"] is True
    assert payload["has_context_usage"] is False


@pytest.mark.asyncio
async def test_usage_repository_marks_context_without_provider_tokens_as_unavailable(usage_db):
    payload = await LLMUsageRepository().aggregate_by_thread("empty_thread")
    assert payload["has_context_usage"] is False
    assert payload["latest_input_tokens"] == 0


@pytest.mark.asyncio
async def test_usage_repository_aggregate_threads_groups_subagents_by_root(usage_db) -> None:
    from agent.modules.conversations.models import ConversationThread
    from agent.shared.infrastructure.db.session import get_async_session

    repository = LLMUsageRepository()
    now = datetime.now(timezone.utc)

    session = await get_async_session()
    async with session:
        session.add(
            ConversationThread(
                thread_id="root_thread",
                platform="api",
                user_id="dashboard",
                channel_id="root_thread",
                agent_name="default",
                title="Root conversation",
                kind="user",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    await repository.record(
        UsageEventInput(
            thread_id="root_thread",
            root_thread_id="root_thread",
            platform="api",
            user_id="dashboard",
            channel_id="root_thread",
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
    await repository.record(
        UsageEventInput(
            thread_id="root_thread:sub:worker:abc12345",
            root_thread_id="root_thread",
            platform="api",
            user_id="dashboard",
            channel_id="root_thread",
            agent_name="worker",
            provider_name="google",
            model_name="gemini-1.5-flash",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=200,
            output_tokens=100,
            total_tokens=300,
            created_at=now + timedelta(seconds=1),
        )
    )
    await repository.record(
        UsageEventInput(
            thread_id="other_thread",
            root_thread_id="other_thread",
            platform="api",
            user_id="dashboard",
            channel_id="other_thread",
            agent_name="default",
            provider_name="openai",
            model_name="gpt-test",
            call_kind="agent",
            internal=False,
            has_usage_metadata=True,
            input_tokens=500,
            output_tokens=500,
            total_tokens=1000,
            created_at=now,
        )
    )

    rows = await repository.aggregate_threads(
        UsageQuery(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1))
    )

    assert rows[0]["thread_id"] == "root_thread"
    assert rows[0]["title"] == "Root conversation"
    assert rows[0]["agent_name"] == "default"
    assert rows[0]["thread_count"] == 2
    assert rows[0]["event_count"] == 2
    assert rows[0]["total_tokens"] == 450
    assert [model["model"] for model in rows[0]["models"]] == [
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]


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
                scope_key="local:/path/to/project_a",
                scope_kind="local",
                scope_label="Project A",
                scope_metadata_json="{}",
                execution_backend="local",
                execution_locator="/path/to/project_a",
                execution_label="Project A",
                execution_metadata_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ThreadWorkspace(
                thread_id="workspace_thread_2",
                scope_key="local:/path/to/project_a",
                scope_kind="local",
                scope_label="Project A",
                scope_metadata_json="{}",
                execution_backend="local",
                execution_locator="/path/to/project_a",
                execution_label="Project A",
                execution_metadata_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ThreadWorkspace(
                thread_id="workspace_thread_3",
                scope_key="local:/path/to/project_b",
                scope_kind="local",
                scope_label="Project B",
                scope_metadata_json="{}",
                execution_backend="local",
                execution_locator="/path/to/project_b",
                execution_label="Project B",
                execution_metadata_json="{}",
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

    data = await repository.aggregate_by_workspace("local:/path/to/project_a")
    assert data["key"] == "local:/path/to/project_a"
    assert data["total_tokens"] == 4500
    assert data["input_tokens"] == 3000
    assert data["output_tokens"] == 1500
    assert len(data["models"]) == 1
    assert data["models"][0]["model"] == "gemini-1.5-pro"
    assert data["models"][0]["calls"] == 2
