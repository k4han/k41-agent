"""Tests for Google Calendar integration: config, auth, repository, client, service, tools, and API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import agent.shared.infrastructure.db.engine as db_engine
from agent.delivery.http.api.google_calendar import router as google_calendar_router
from agent.modules.google_calendar import (
    CalendarEventItem,
    GoogleCalendarAccount,
    GoogleCalendarClient,
    GoogleCalendarService,
    GoogleCalendarSettings,
    GoogleCalendarStatus,
    GoogleCalendarStore,
    GoogleOAuthManager,
    get_google_calendar_settings,
    migrate_google_calendar_tables,
)
from agent.modules.tools.builtin.google_calendar.tools import (
    calendar_check_availability,
    calendar_create_event,
    calendar_delete_event,
    calendar_get_agenda,
    calendar_list_events,
    calendar_update_event,
)
from agent.shared.infrastructure.db.base import Base
from agent.shared.infrastructure.db.engine import (
    close_async_engine,
    initialize_async_engine,
)
from agent.shared.infrastructure.db.models import load_orm_models


@pytest_asyncio.fixture
async def calendar_test_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Set up an isolated SQLite database for Google Calendar testing."""
    db_path = tmp_path / "calendar-test.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"

    monkeypatch.setattr(db_engine, "DEFAULT_DATABASE_URL", db_url)
    monkeypatch.setattr(db_engine, "_cached_database_url", None)
    monkeypatch.setattr(db_engine, "_async_engine", None)
    monkeypatch.setattr(db_engine, "_async_session_maker", None)
    monkeypatch.setattr(db_engine, "_tables_created", False)

    from agent.shared.config import attach_database_config_source, detach_database_config_source

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)
    attach_database_config_source(db_url)
    try:
        yield db_url
    finally:
        detach_database_config_source()
        await close_async_engine()


# 1. Config Tests
def test_google_calendar_settings_configuration():
    settings = GoogleCalendarSettings(
        enabled=True,
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:4141/integrations/google/callback",
    )
    assert settings.is_configured is True
    assert "https://www.googleapis.com/auth/calendar.events" in settings.scopes

    unconfigured = GoogleCalendarSettings(
        enabled=True,
        client_id="",
        client_secret="",
        redirect_uri="",
    )
    assert unconfigured.is_configured is False


# 2. Repository & Encryption Tests
@pytest.mark.asyncio
async def test_calendar_store_token_encryption_and_crud(calendar_test_db, tmp_path):
    key_path = tmp_path / "fernet.key"
    store = GoogleCalendarStore(key_path=key_path)

    # Test encryption / decryption
    plain = "my-secret-refresh-token-12345"
    enc = store.encrypt_token(plain)
    assert enc != plain
    assert store.decrypt_token(enc) == plain

    # Test Save Account
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    account = await store.save_account(
        user_id="user_1",
        account_email="test@gmail.com",
        refresh_token=plain,
        access_token="initial-access-token",
        token_expiry=expiry,
        scopes="https://www.googleapis.com/auth/calendar.events",
    )
    assert account.account_email == "test@gmail.com"
    assert account.user_id == "user_1"

    # Test Get Account
    fetched = await store.get_account("user_1")
    assert fetched is not None
    assert fetched.account_email == "test@gmail.com"
    assert store.decrypt_token(fetched.encrypted_refresh_token) == plain

    # Test Update Access Token
    new_expiry = datetime.now(timezone.utc) + timedelta(hours=2)
    await store.update_access_token(fetched.id, "new-access-token", new_expiry)
    updated = await store.get_account("user_1")
    assert updated.access_token == "new-access-token"

    # Test Delete Account
    deleted = await store.delete_account("user_1")
    assert deleted is True
    assert await store.get_account("user_1") is None


# 3. OAuth Manager Tests
def test_oauth_manager_get_authorization_url(tmp_path):
    settings = GoogleCalendarSettings(
        enabled=True,
        client_id="mock-client-id",
        client_secret="mock-client-secret",
        redirect_uri="http://localhost:4141/integrations/google/callback",
    )
    oauth = GoogleOAuthManager(settings=settings)
    auth_url = oauth.get_authorization_url(state="test_state_123")
    assert "https://accounts.google.com/o/oauth2/v2/auth" in auth_url
    assert "client_id=mock-client-id" in auth_url
    assert "state=test_state_123" in auth_url
    assert "access_type=offline" in auth_url


@pytest.mark.asyncio
async def test_oauth_manager_exchange_code(calendar_test_db, tmp_path):
    settings = GoogleCalendarSettings(
        enabled=True,
        client_id="mock-client-id",
        client_secret="mock-client-secret",
        redirect_uri="http://localhost:4141/integrations/google/callback",
    )
    key_path = tmp_path / "fernet.key"
    store = GoogleCalendarStore(key_path=key_path)
    oauth = GoogleOAuthManager(settings=settings, store=store)

    fake_token_resp = MagicMock(status_code=200)
    fake_token_resp.json.return_value = {
        "access_token": "mock-access-token",
        "refresh_token": "mock-refresh-token",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/calendar.events",
    }

    fake_userinfo_resp = MagicMock(status_code=200)
    fake_userinfo_resp.json.return_value = {"email": "user@example.com"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_post.return_value = fake_token_resp
        mock_get.return_value = fake_userinfo_resp

        result = await oauth.exchange_code(code="test_code", user_id="default")
        assert result["email"] == "user@example.com"
        assert result["connected"] is True

        # Verify account was saved in database
        saved = await store.get_account("default")
        assert saved is not None
        assert saved.account_email == "user@example.com"
        assert store.decrypt_token(saved.encrypted_refresh_token) == "mock-refresh-token"


@pytest.mark.asyncio
async def test_oauth_manager_valid_token_with_naive_expiry(calendar_test_db, tmp_path):
    """SQLite strips tzinfo, so a naive token_expiry must not raise TypeError."""
    settings = GoogleCalendarSettings(
        enabled=True,
        client_id="mock-client-id",
        client_secret="mock-client-secret",
        redirect_uri="http://localhost:4141/integrations/google/callback",
    )
    key_path = tmp_path / "fernet.key"
    store = GoogleCalendarStore(key_path=key_path)
    oauth = GoogleOAuthManager(settings=settings, store=store)

    naive_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    account = await store.save_account(
        user_id="default",
        account_email="user@example.com",
        refresh_token="refresh-token",
        access_token="cached-access-token",
        token_expiry=naive_expiry,
        scopes="https://www.googleapis.com/auth/calendar.events",
    )
    assert account.token_expiry is not None
    assert account.token_expiry.tzinfo is None

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        token = await oauth.get_valid_access_token("default")
        assert token == "cached-access-token"
        mock_post.assert_not_called()


# 4. Client Tests
@pytest.mark.asyncio
async def test_calendar_client_list_events():
    mock_oauth = MagicMock()
    mock_oauth.get_valid_access_token = AsyncMock(return_value="valid-token")

    client = GoogleCalendarClient(oauth_manager=mock_oauth)

    fake_api_resp = MagicMock(status_code=200)
    fake_api_resp.json.return_value = {
        "items": [
            {
                "id": "ev_1",
                "summary": "Team Sync",
                "start": {"dateTime": "2026-09-28T09:00:00+07:00"},
                "end": {"dateTime": "2026-09-28T10:00:00+07:00"},
                "hangoutLink": "https://meet.google.com/abc-def-ghi",
                "attendees": [{"email": "colleague@example.com"}],
            }
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_api_resp
        events = await client.list_events(user_id="default")
        assert len(events) == 1
        assert events[0].id == "ev_1"
        assert events[0].summary == "Team Sync"
        assert events[0].meet_link == "https://meet.google.com/abc-def-ghi"
        assert "colleague@example.com" in events[0].attendees


@pytest.mark.asyncio
async def test_calendar_client_create_and_delete_event():
    mock_oauth = MagicMock()
    mock_oauth.get_valid_access_token = AsyncMock(return_value="valid-token")

    client = GoogleCalendarClient(oauth_manager=mock_oauth)

    fake_create_resp = MagicMock(status_code=200)
    fake_create_resp.json.return_value = {
        "id": "new_ev_123",
        "summary": "Design Sprint",
        "start": {"dateTime": "2026-09-29T14:00:00+07:00"},
        "end": {"dateTime": "2026-09-29T15:00:00+07:00"},
        "htmlLink": "https://calendar.google.com/event?id=123",
        "hangoutLink": "https://meet.google.com/xyz-uvw-rst",
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = fake_create_resp
        ev = await client.create_event(
            summary="Design Sprint",
            start_time="2026-09-29T14:00:00+07:00",
            end_time="2026-09-29T15:00:00+07:00",
            create_meet=True,
        )
        assert ev.id == "new_ev_123"
        assert ev.meet_link == "https://meet.google.com/xyz-uvw-rst"

    fake_delete_resp = MagicMock(status_code=204)
    with patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_del:
        mock_del.return_value = fake_delete_resp
        deleted = await client.delete_event("new_ev_123")
        assert deleted is True


# 5. Service & Agenda Formatting Tests
@pytest.mark.asyncio
async def test_calendar_service_agenda_formatting():
    mock_client = MagicMock()
    mock_client.list_events = AsyncMock(
        return_value=[
            CalendarEventItem(
                id="ev_1",
                summary="Morning Standup",
                start="2026-09-28T09:00:00+07:00",
                end="2026-09-28T09:30:00+07:00",
                meet_link="https://meet.google.com/abc-xyz",
            ),
            CalendarEventItem(
                id="ev_2",
                summary="Client Presentation",
                start="2026-09-28T14:00:00+07:00",
                end="2026-09-28T15:00:00+07:00",
                location="Room 302",
            ),
        ]
    )
    service = GoogleCalendarService(client=mock_client)
    agenda = await service.get_agenda(date_str="2026-09-28", tz_name="Asia/Ho_Chi_Minh")

    assert "Agenda for Monday, 2026-09-28" in agenda
    assert "Morning Standup" in agenda
    assert "09:00 - 09:30" in agenda
    assert "[Google Meet](https://meet.google.com/abc-xyz)" in agenda
    assert "Client Presentation" in agenda
    assert "(Room 302)" in agenda


# 6. Built-in Tools Execution Tests
@pytest.mark.asyncio
async def test_calendar_tools_execution():
    mock_service = MagicMock()
    mock_service.list_events = AsyncMock(
        return_value=[
            CalendarEventItem(
                id="ev_1",
                summary="Sprint Demo",
                start="2026-09-28T10:00:00+07:00",
                end="2026-09-28T11:00:00+07:00",
            )
        ]
    )
    mock_service.create_event = AsyncMock(
        return_value=CalendarEventItem(
            id="ev_created_1",
            summary="New Event",
            start="2026-09-28T15:00:00+07:00",
            end="2026-09-28T16:00:00+07:00",
            meet_link="https://meet.google.com/new-meet",
        )
    )
    mock_service.delete_event = AsyncMock(return_value=True)

    with patch("agent.modules.tools.builtin.google_calendar.tools.get_google_calendar_service", return_value=mock_service):
        # Test list events tool
        list_res = await calendar_list_events.ainvoke({"time_min": "2026-09-28T00:00:00Z"})
        assert "Sprint Demo" in list_res

        # Test create event tool
        create_res = await calendar_create_event.ainvoke({
            "summary": "New Event",
            "start_time": "2026-09-28T15:00:00+07:00",
            "end_time": "2026-09-28T16:00:00+07:00",
            "create_meet": True,
        })
        assert "Successfully created event" in create_res
        assert "Google Meet: https://meet.google.com/new-meet" in create_res

        # Test delete event tool
        delete_res = await calendar_delete_event.ainvoke({"event_id": "ev_created_1"})
        assert "was successfully deleted" in delete_res


# 7. HTTP API Router Tests
def test_google_calendar_api_routes(calendar_test_db, monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", "my-client-id")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", "my-secret")
    monkeypatch.setenv(
        "GOOGLE_CALENDAR_REDIRECT_URI",
        "http://localhost:4141/integrations/google/callback",
    )

    app = FastAPI()
    app.include_router(google_calendar_router)
    client = TestClient(app)

    # Test GET config reads platform env
    cfg_resp = client.get("/integrations/google/config")
    assert cfg_resp.status_code == 200
    assert "client_id" in cfg_resp.json()

    # Test POST config is blocked: OAuth App is env-managed
    update_resp = client.post(
        "/integrations/google/config",
        json={
            "client_id": "my-client-id",
            "client_secret": "my-secret",
            "redirect_uri": "http://localhost:4141/integrations/google/callback",
        },
    )
    assert update_resp.status_code == 403

    # Verify config still comes from env
    cfg_resp2 = client.get("/integrations/google/config")
    assert cfg_resp2.json()["client_id"] == "my-client-id"
    assert cfg_resp2.json()["client_secret_configured"] is True

    # Test GET auth-url
    url_resp = client.get("/integrations/google/auth-url")
    assert url_resp.status_code == 200
    assert "https://accounts.google.com" in url_resp.json()["url"]

    # Test GET status
    status_resp = client.get("/integrations/google/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["connected"] is False

    # Test POST disconnect
    disc_resp = client.post("/integrations/google/disconnect")
    assert disc_resp.status_code == 200
    assert disc_resp.json() == {"success": True}

    # Test callback error page renders access_denied guidance without XSS
    err_resp = client.get(
        "/integrations/google/callback",
        params={"error": "access_denied", "error_description": "<script>alert(1)</script>"},
    )
    assert err_resp.status_code == 200
    assert "Test users" in err_resp.text
    assert "<script>alert(1)</script>" not in err_resp.text

