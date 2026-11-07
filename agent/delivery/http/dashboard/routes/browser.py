"""Authenticated browser management and ticket-authenticated viewer sockets."""

import asyncio
import contextlib
import json
import logging
import os
from itertools import count
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from agent.bootstrap.container import get_container
from agent.bootstrap.browser import find_browser, installation_unavailable_reason
from agent.bootstrap.browser_setup import BrowserSetupBusyError, BrowserSetupManager
from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.modules.admin_auth import get_current_admin
from agent.modules.browser import BrowserAccessDenied, BrowserError, CDPViewer
from agent.delivery.http.dashboard.routes.helpers.browser_stream import SEND_TIMEOUT, BrowserStreamState, stream_tab

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard-api/browser")
viewer_router = APIRouter()


def setup_manager(request: Request) -> BrowserSetupManager:
    manager = getattr(request.app.state, "browser_setup_manager", None)
    if manager is None:
        manager = BrowserSetupManager()
        request.app.state.browser_setup_manager = manager
    return manager


class BrowserSetupBody(BaseModel):
    action: Literal["install", "repair", "check"]


@router.get("/configuration")
async def browser_configuration(request: Request, admin: str = Depends(get_current_admin)):
    config = get_request_config_service(request)
    settings, sources = config.get_settings_overview_and_sources()
    settings = {key: info for key, info in settings.items() if key.startswith("browser.")}
    return {"settings": settings, "by_category": {"browser": settings},
            "settings_sources": {key: info for key, info in sources.items() if key in settings}}


@router.get("/setup")
async def browser_setup_status(request: Request, admin: str = Depends(get_current_admin)):
    configured = get_request_config_service(request).get_str("browser.executable_path", "")
    detected = find_browser()
    executable = configured or detected
    installed = bool(executable and Path(executable).is_file() and (os.name == "nt" or os.access(executable, os.X_OK)))
    try:
        reason = installation_unavailable_reason()
    except OSError as exc:
        reason = f"Could not detect the host distribution: {exc}"
    return {
        "installed": installed, "executable_path": executable,
        "configured_executable_path": configured,
        "installation_supported": reason is None, "installation_unavailable_reason": reason,
        "job": setup_manager(request).status(),
    }


@router.post("/setup", status_code=202)
async def start_browser_setup(body: BrowserSetupBody, request: Request, admin: str = Depends(get_current_admin)):
    if body.action != "check":
        try:
            reason = installation_unavailable_reason()
        except OSError as exc:
            raise HTTPException(400, f"Could not detect the host distribution: {exc}") from exc
        if reason:
            raise HTTPException(400, reason)
    configured = get_request_config_service(request).get_str("browser.executable_path", "")
    try:
        return setup_manager(request).start(body.action, configured)
    except BrowserSetupBusyError as exc:
        raise HTTPException(409, str(exc)) from None


def service(request: Request):
    return get_container(request).browser_service


def owner(admin: str) -> str:
    return f"admin:{admin}"


async def checked(operation):
    try:
        return await operation
    except BrowserAccessDenied as exc:
        raise HTTPException(403, str(exc)) from None
    except PermissionError:
        # The text of a file system error contains server paths.
        logger.warning("Browser storage operation failed.", exc_info=True)
        raise HTTPException(500, "Browser storage is not accessible. Check the server logs.") from None
    except BrowserError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from None


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class BindingBody(BaseModel):
    profile_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ProjectProfileBody(BindingBody):
    mode: Literal["inherit", "separate", "profile"] | None = None

    @model_validator(mode="after")
    def validate_choice(self):
        self.mode = self.mode or ("profile" if self.profile_id else "separate")
        if (self.mode == "profile") != (self.profile_id is not None):
            raise ValueError("Select a profile only when the project uses a specific profile.")
        return self


def profile_name(body: ProfileBody) -> str:
    if not body.name.strip():
        raise HTTPException(422, "Profile name is required.")
    return body.name.strip()


@router.get("/profiles")
async def profiles(admin: str = Depends(get_current_admin), browser=Depends(service)):
    return {"profiles": await browser.profiles(owner(admin))}


@router.post("/profiles")
async def create_profile(body: ProfileBody, admin: str = Depends(get_current_admin), browser=Depends(service)):
    return await checked(browser.create_profile(owner(admin), profile_name(body)))


@router.patch("/profiles/{profile_id}")
async def rename_profile(profile_id: str, body: ProfileBody, admin: str = Depends(get_current_admin), browser=Depends(service)):
    return await checked(browser.rename_profile(owner(admin), profile_id, profile_name(body)))


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str, admin: str = Depends(get_current_admin), browser=Depends(service)):
    removed = await checked(browser.delete_profile(owner(admin), profile_id))
    return {"deleted": True, "saved_data_removed": removed}


@router.get("/projects/{project_id}/profile")
async def project_profile(project_id: int = PathParam(ge=1), admin: str = Depends(get_current_admin), browser=Depends(service)):
    try:
        return await browser.repository.project_profile_settings(project_id, owner(admin))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


@router.put("/projects/{project_id}/profile")
async def set_project_profile(body: ProjectProfileBody, project_id: int = PathParam(ge=1), admin: str = Depends(get_current_admin), browser=Depends(service)):
    try:
        await checked(browser.repository.set_project_profile(project_id, owner(admin), body.profile_id, body.mode))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    return await browser.repository.project_profile_settings(project_id, owner(admin))


@router.get("/default-profile")
async def default_profile(admin: str = Depends(get_current_admin), browser=Depends(service)):
    return {"profile_id": await browser.repository.default_profile(owner(admin))}


@router.put("/default-profile")
async def set_default_profile(body: BindingBody, admin: str = Depends(get_current_admin), browser=Depends(service)):
    async with browser.operation_scope(f"profile:{body.profile_id}"):
        await checked(browser.repository.set_default_profile(owner(admin), body.profile_id))
    return {"profile_id": body.profile_id}


@router.get("/threads/{thread_id}")
async def status(thread_id: str, admin: str = Depends(get_current_admin), browser=Depends(service)):
    return await checked(browser.status(thread_id, owner(admin)))


@router.put("/threads/{thread_id}")
async def select_profile(thread_id: str, body: BindingBody, admin: str = Depends(get_current_admin), browser=Depends(service)):
    await checked(browser.select_profile(thread_id, owner(admin), body.profile_id))
    return await checked(browser.status(thread_id, owner(admin)))


@router.post("/threads/{thread_id}")
async def open_browser(thread_id: str, admin: str = Depends(get_current_admin), browser=Depends(service)):
    await checked(browser.ensure(thread_id, owner(admin)))
    return await checked(browser.status(thread_id, owner(admin)))


@router.delete("/threads/{thread_id}")
async def close_browser(thread_id: str, admin: str = Depends(get_current_admin), browser=Depends(service)):
    await checked(browser.close(thread_id, owner(admin)))
    return await checked(browser.status(thread_id, owner(admin)))


@router.post("/threads/{thread_id}/viewer-ticket")
async def viewer_ticket(thread_id: str, admin: str = Depends(get_current_admin), browser=Depends(service)):
    return {"ticket": await checked(browser.issue_ticket(thread_id, owner(admin)))}


@router.get("/artifacts/{artifact_id}")
async def artifact(artifact_id: str, admin: str = Depends(get_current_admin), browser=Depends(service)):
    row = await browser.repository.artifact(owner(admin), artifact_id)
    if row is None:
        raise HTTPException(404, "Browser image not found.")
    path = browser.root / "images" / row.artifact_id
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Browser image not found.")
    return FileResponse(path, media_type=row.mime_type, headers={"Cache-Control": "private, no-store"})


def origin_allowed(websocket: WebSocket, config) -> bool:
    origin = websocket.headers.get("origin", "")
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    own_origin = f"{scheme}://{websocket.headers.get('host', '')}"
    allowed = config.get("security.cors_origins", []) or []
    if isinstance(allowed, str):
        allowed = [value.strip() for value in allowed.split(",")]
    return bool(origin) and origin in {own_origin, *allowed}


async def viewer_target(current, tabs, fallback=None):
    tab_ids = {tab["id"] for tab in tabs}
    active_tab = await current.driver.active_tab_id() if hasattr(current.driver, "active_tab_id") else None
    return next((tab_id for tab_id in (current.active_tab_id, active_tab, fallback) if tab_id in tab_ids), tabs[0]["id"])


async def view_tab(websocket, browser, current, target_id, viewer_id, follow_agent=True, frame_ids=None, state=None):
    state = state or BrowserStreamState(frame_ids=frame_ids if frame_ids is not None else count())
    return await stream_tab(websocket, browser, current, target_id, viewer_id, CDPViewer, viewer_target,
                            follow_agent, state)


@viewer_router.websocket("/dashboard-api/browser/viewer")
async def browser_viewer(websocket: WebSocket):
    container = get_container(websocket)
    if not origin_allowed(websocket, container.config_service):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    current = None
    viewer_id = uuid4().hex
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), 8)
        if len(raw) > 1024:
            raise PermissionError("Invalid viewer ticket.")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise PermissionError("Invalid viewer ticket.")
        current = container.browser_service.consume_ticket(payload.get("ticket", ""))
        tabs = await current.driver.tabs()
        if not tabs:
            raise ValueError("The browser has no visible tabs.")
        pinned = payload.get("tab_id")
        follow_agent = payload.get("follow_agent") is not False
        target_id = pinned if not follow_agent and pinned in {tab["id"] for tab in tabs} else await viewer_target(current, tabs)
        revision = payload.get("visibility_revision", 0)
        state = BrowserStreamState(visible=payload.get("visible") is not False,
            visibility_revision=revision if type(revision) is int and 0 <= revision <= 0xFFFFFFFF else 0)
        while not current.closed:
            target_id, follow_agent = await view_tab(websocket, container.browser_service, current, target_id,
                                                    viewer_id, follow_agent, state=state)
    except (WebSocketDisconnect, ConnectionError, TimeoutError):
        pass
    except Exception:
        # Do not log input events: they may contain login credentials.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.send_json({"type": "error", "message":
                "Browser connection ended. Refresh the viewer to reconnect."}), SEND_TIMEOUT)
    finally:
        if current and current.controller == viewer_id:
            current.controller = None
            current.notify()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.close(), SEND_TIMEOUT)
