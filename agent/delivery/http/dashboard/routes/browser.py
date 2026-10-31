"""Authenticated browser management and ticket-authenticated viewer sockets."""

import asyncio
import contextlib
import json
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.bootstrap.container import get_container
from agent.modules.admin_auth import get_current_admin
from agent.modules.browser import BrowserError, CDPViewer

router = APIRouter(prefix="/dashboard-api/browser")
viewer_router = APIRouter()


def service(request: Request):
    return get_container(request).browser_service


def owner(admin: str) -> str:
    return f"admin:{admin}"


async def checked(operation):
    try:
        return await operation
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except BrowserError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from None


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class BindingBody(BaseModel):
    profile_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


@router.get("/profiles")
async def profiles(admin: str = Depends(get_current_admin), browser=Depends(service)):
    return {"profiles": await browser.repository.profiles(owner(admin))}


@router.post("/profiles")
async def create_profile(body: ProfileBody, admin: str = Depends(get_current_admin), browser=Depends(service)):
    if not body.name.strip():
        raise HTTPException(422, "Profile name is required.")
    return await browser.repository.create_profile(owner(admin), body.name.strip())


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str, admin: str = Depends(get_current_admin), browser=Depends(service)):
    await checked(browser.delete_profile(owner(admin), profile_id))
    return {"deleted": True}


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


async def view_tab(websocket, browser, current, target_id, viewer_id):
    async with CDPViewer(current.driver, target_id) as viewer:
        async def frames():
            last_status = 0
            while not current.closed:
                if time.monotonic() - last_status > 1.5:
                    await websocket.send_json({"type": "status", "tabs": await current.driver.tabs(),
                        "tab_id": target_id,
                        "mode": "human" if current.handoff_id else "agent",
                        "can_control": bool(current.handoff_id and current.controller in {None, viewer_id})})
                    last_status = time.monotonic()
                try:
                    frame = await asyncio.wait_for(viewer.frames.get(), 1)
                    await websocket.send_json({"type": "frame", "data": frame["data"], "metadata": frame["metadata"]})
                    await asyncio.sleep(0.1)
                except TimeoutError:
                    pass
            raise ConnectionError("Browser session closed.")

        async def inputs():
            while True:
                raw = await websocket.receive_text()
                if len(raw) > 65536:
                    raise ValueError("Viewer input exceeds the message limit.")
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError("Invalid viewer input.")
                if event.get("type") == "select_tab":
                    tab_id = event.get("tab_id")
                    if tab_id not in {tab["id"] for tab in await current.driver.tabs()}:
                        raise ValueError("Browser tab no longer exists.")
                    async with current.lock:
                        if current.handoff_id and current.controller in {None, viewer_id}:
                            current.controller = viewer_id
                            current.active_tab_id = tab_id
                            current.last_activity = time.monotonic()
                    return tab_id
                async with current.lock:
                    if current.closed or not current.handoff_id or current.controller not in {None, viewer_id}:
                        await websocket.send_json({"type": "error", "message": "Browser input is available only during your handoff."})
                        continue
                    current.controller = viewer_id
                    current.active_tab_id = target_id
                    await viewer.activate()
                    await viewer.input(event)
                    current.last_activity = time.monotonic()

        sending = asyncio.create_task(frames())
        receiving = asyncio.create_task(inputs())
        try:
            done, _ = await asyncio.wait([sending, receiving], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            return receiving.result()
        finally:
            sending.cancel()
            receiving.cancel()
            await asyncio.gather(sending, receiving, return_exceptions=True)


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
        current = container.browser_service.consume_ticket(payload.get("ticket", ""))
        tabs = await current.driver.tabs()
        if not tabs:
            raise ValueError("The browser has no visible tabs.")
        active_tab = await current.driver.active_tab_id() if hasattr(current.driver, "active_tab_id") else None
        target_id = current.active_tab_id or active_tab
        if target_id not in {tab["id"] for tab in tabs}:
            target_id = tabs[0]["id"]
        while not current.closed:
            target_id = await view_tab(websocket, container.browser_service, current, target_id, viewer_id)
    except (WebSocketDisconnect, ConnectionError):
        pass
    except Exception:
        # Do not log input events: they may contain login credentials.
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "message": "Browser connection ended. Refresh the viewer to reconnect."})
    finally:
        if current and current.controller == viewer_id:
            current.controller = None
        with contextlib.suppress(Exception):
            await websocket.close()
