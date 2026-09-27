from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.settings import (
    ensure_runtime_keys,
    normalize_setting_updates,
    update_config_settings,
)
from agent.modules.google_calendar import (
    get_google_calendar_service,
    get_google_calendar_settings,
    get_google_oauth_manager,
)

router = APIRouter()

DEFAULT_USER_ID = "default"


class GoogleCalendarDashboardConfigBody(BaseModel):
    """Request body for updating Google Calendar settings from the dashboard."""

    enabled: bool | None = Field(default=None, description="Enable Google Calendar integration.")
    client_id: str | None = Field(default=None, description="Google Cloud OAuth Client ID.")
    client_secret: str | None = Field(default=None, description="Google Cloud OAuth Client Secret.")
    redirect_uri: str | None = Field(default=None, description="OAuth redirect URI.")


@router.get("/dashboard-api/google-calendar")
async def get_dashboard_google_calendar() -> dict[str, Any]:
    """Get Google Calendar status and masked config for the dashboard."""
    service = get_google_calendar_service()
    settings = get_google_calendar_settings()
    status = await service.get_status(user_id=DEFAULT_USER_ID)
    accounts = []
    try:
        from agent.modules.google_calendar import get_google_calendar_store

        store = get_google_calendar_store()
        for account in await store.list_accounts():
            accounts.append(
                {
                    "email": account.account_email,
                    "user_id": account.user_id,
                    "is_primary": bool(account.is_primary),
                    "updated_at": account.updated_at.isoformat() if account.updated_at else None,
                }
            )
    except Exception:
        accounts = []
    return {
        "connected": status.connected,
        "email": status.email,
        "configured": status.configured or settings.is_configured,
        "enabled": settings.enabled,
        "client_id": settings.client_id,
        "client_secret_configured": bool(settings.client_secret),
        "redirect_uri": settings.redirect_uri,
        "scopes": status.scopes,
        "updated_at": status.updated_at.isoformat() if status.updated_at else None,
        "accounts": accounts,
    }


@router.put("/dashboard-api/google-calendar/config")
async def update_dashboard_google_calendar_config(
    body: GoogleCalendarDashboardConfigBody,
    request: Request,
) -> dict[str, Any]:
    """Update Google Calendar runtime settings from the dashboard."""
    values: dict[str, Any] = {}
    if body.enabled is not None:
        values["google_calendar.enabled"] = bool(body.enabled)
    if body.client_id is not None and body.client_id.strip():
        values["google_calendar.client_id"] = body.client_id.strip()
    if body.client_secret is not None and body.client_secret.strip():
        values["google_calendar.client_secret"] = body.client_secret.strip()
    if body.redirect_uri is not None and body.redirect_uri.strip():
        values["google_calendar.redirect_uri"] = body.redirect_uri.strip()
    if not values:
        raise HTTPException(status_code=400, detail="No Google Calendar settings to update.")
    ensure_runtime_keys(list(values))
    normalized = normalize_setting_updates(values)
    service = get_request_config_service(request)
    update_config_settings(service, normalized)
    settings = get_google_calendar_settings()
    return {
        "status": "success",
        "updated": sorted(values.keys()),
        "configured": settings.is_configured,
    }


@router.get("/dashboard-api/google-calendar/auth-url")
async def get_dashboard_google_calendar_auth_url(redirect_uri: str | None = None) -> dict[str, str]:
    """Generate a Google OAuth authorization URL for the dashboard flow."""
    oauth = get_google_oauth_manager()
    state = secrets.token_urlsafe(16)
    try:
        url = oauth.get_authorization_url(state=state, redirect_uri=redirect_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url, "state": state}


@router.post("/dashboard-api/google-calendar/disconnect")
async def disconnect_dashboard_google_calendar() -> dict[str, bool]:
    """Revoke and delete the stored Google Calendar credentials."""
    oauth = get_google_oauth_manager()
    success = await oauth.revoke_account(user_id=DEFAULT_USER_ID)
    return {"success": success}


__all__ = ["router"]
