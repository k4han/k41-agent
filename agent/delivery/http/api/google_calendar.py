"""HTTP API endpoints for Google Calendar OAuth and settings."""

from __future__ import annotations

import html
import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from agent.modules.google_calendar import (
    GoogleCalendarSettings,
    get_google_calendar_service,
    get_google_calendar_settings,
    get_google_oauth_manager,
)
from agent.shared.config import get_config_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/google", tags=["google_calendar"])


class GoogleCalendarConfigRequest(BaseModel):
    client_id: str = Field(..., description="Google Cloud OAuth Client ID")
    client_secret: str | None = Field(default=None, description="Google Cloud OAuth Client Secret")
    redirect_uri: str | None = Field(default=None, description="OAuth redirect URI")


@router.get("/status")
async def get_google_calendar_status(user_id: str = "default") -> dict[str, Any]:
    """Get connection status and configured email."""
    service = get_google_calendar_service()
    status = await service.get_status(user_id=user_id)
    return status.model_dump()


@router.get("/auth-url")
async def get_google_auth_url(redirect_uri: str | None = None) -> dict[str, str]:
    """Generate OAuth 2.0 authorization URL for Google Calendar."""
    oauth = get_google_oauth_manager()
    state = secrets.token_urlsafe(16)
    try:
        url = oauth.get_authorization_url(state=state, redirect_uri=redirect_uri)
        return {"url": url, "state": state}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/callback", response_class=HTMLResponse)
async def google_oauth_callback(
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    error_subtype: str | None = None,
    state: str | None = None,
    user_id: str = "default",
) -> str:
    """Handle OAuth redirect callback from Google."""
    if error:
        logger.warning(
            "Google OAuth callback error: %s (description=%s subtype=%s)",
            error,
            error_description,
            error_subtype,
        )
        safe_error = html.escape(error)
        extra_detail = f"<p style='color:#9ca3af;font-size:0.85rem;'>{html.escape(error_description)}</p>" if error_description else ""
        help_block = ""
        if error == "access_denied":
            help_block = """
                    <div style="text-align:left;margin:1rem auto 0;max-width:520px;background:#111827;border:1px solid #374151;border-radius:8px;padding:1rem 1.25rem;font-size:0.85rem;line-height:1.6;">
                        <p style="margin:0 0 0.5rem;color:#fbbf24;font-weight:600;">403 access_denied usually means a Google Cloud Console setup issue (not an app bug). Check in order:</p>
                        <ol style="margin:0;padding-left:1.25rem;color:#d1d5db;">
                            <li><strong>Test users:</strong> OAuth consent screen &gt; Audience &gt; while Publishing status is <em>Testing</em>, add the Google account you sign in with under <em>Test users</em>. Or publish the app to Production.</li>
                            <li><strong>Scopes:</strong> OAuth consent screen &gt; Data Access &gt; add <code>.../auth/calendar.events</code>, <code>.../auth/calendar.readonly</code>, <code>.../auth/userinfo.email</code>, <code>openid</code>.</li>
                            <li><strong>API enabled:</strong> APIs &amp; Services &gt; enable <em>Google Calendar API</em> in the same project as this Client ID.</li>
                            <li><strong>Right account:</strong> sign in with the test-user account (try an incognito window). Workspace accounts may also be blocked by admin policy.</li>
                            <li><strong>Redirect URI:</strong> Credentials &gt; this OAuth Client ID &gt; Authorized redirect URIs must contain this exact value (no trailing slash):<br><code>http://localhost:4141/integrations/google/callback</code></li>
                        </ol>
                    </div>"""
        return f"""
        <html>
            <body style="font-family: sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #121212; color: #fff;">
                <div style="text-align: center; padding: 2rem; border-radius: 8px; background: #1e1e1e; max-width: 640px;">
                    <h2 style="color: #ef4444;">Google Authorization Failed</h2>
                    <p>{safe_error}</p>
                    {extra_detail}
                    {help_block}
                    <button onclick="window.close()" style="margin-top: 1rem; padding: 0.5rem 1rem; background: #374151; color: white; border: none; border-radius: 4px; cursor: pointer;">Close Window</button>
                </div>
            </body>
        </html>
        """

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    oauth = get_google_oauth_manager()
    try:
        result = await oauth.exchange_code(code=code, user_id=user_id)
        email = result.get("email", "")
        safe_email = html.escape(str(email))
        return f"""
        <html>
            <body style="font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #121212; color: #fff;">
                <div style="text-align: center; padding: 2rem; border-radius: 8px; background: #1e1e1e;">
                    <h2 style="color: #22c55e;">Google Calendar Connected!</h2>
                    <p>Successfully linked account: <strong>{safe_email}</strong></p>
                    <p style="color: #9ca3af; font-size: 0.9rem;">You can now close this tab and return to the dashboard.</p>
                    <script>
                        if (window.opener) {{
                            window.opener.postMessage({{ type: 'GOOGLE_CALENDAR_CONNECTED', email: {json.dumps(str(email))} }}, '*');
                            setTimeout(() => window.close(), 1500);
                        }}
                    </script>
                </div>
            </body>
        </html>
        """
    except Exception as exc:
        logger.error("Failed to complete Google OAuth exchange: %s", exc)
        safe_exc = html.escape(str(exc))
        return f"""
        <html>
            <body style="font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #121212; color: #fff;">
                <div style="text-align: center; padding: 2rem; border-radius: 8px; background: #1e1e1e;">
                    <h2 style="color: #ef4444;">Exchange Error</h2>
                    <p>{safe_exc}</p>
                </div>
            </body>
        </html>
        """


@router.post("/disconnect")
async def disconnect_google_calendar(user_id: str = "default") -> dict[str, bool]:
    """Disconnect and revoke stored credentials for the Google Calendar integration."""
    oauth = get_google_oauth_manager()
    success = await oauth.revoke_account(user_id=user_id)
    return {"success": success}


@router.get("/config")
async def get_google_calendar_config() -> dict[str, Any]:
    """Get current configuration status (credentials masked)."""
    settings = get_google_calendar_settings()
    return {
        "enabled": settings.enabled,
        "client_id": settings.client_id,
        "client_secret_configured": bool(settings.client_secret),
        "redirect_uri": settings.redirect_uri,
        "is_configured": settings.is_configured,
    }


@router.post("/config")
async def update_google_calendar_config(payload: GoogleCalendarConfigRequest) -> dict[str, bool]:
    """Update Google OAuth Client ID and Secret in runtime settings."""
    cfg = get_config_service()
    cfg.update_setting("google_calendar.client_id", payload.client_id.strip())
    if payload.client_secret is not None and payload.client_secret.strip():
        cfg.update_setting("google_calendar.client_secret", payload.client_secret.strip())
    if payload.redirect_uri is not None and payload.redirect_uri.strip():
        cfg.update_setting("google_calendar.redirect_uri", payload.redirect_uri.strip())

    return {"success": True}


__all__ = ["router"]
