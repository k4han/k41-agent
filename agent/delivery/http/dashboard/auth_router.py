from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from agent.delivery.http.dashboard.spa import spa_index_response
from agent.modules.admin_auth import (
    create_access_token,
    get_admin_auth_service,
    get_current_admin,
)

router = APIRouter(tags=["auth"])


def _wants_json(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    accept = request.headers.get("accept", "")
    return "application/json" in content_type or "application/json" in accept


async def _read_body(request: Request) -> dict[str, Any]:
    if "application/json" not in request.headers.get("content-type", ""):
        return {}
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@router.get("/login")
async def login_get() -> Response:
    return spa_index_response()


@router.post("/login")
async def login_post(request: Request, password: str | None = Form(default=None)) -> Response:
    body = await _read_body(request)
    password_value = str(body.get("password") or password or "")

    auth_service = get_admin_auth_service()
    admin = await auth_service.authenticate(password_value)
    if admin is None:
        if _wants_json(request):
            return JSONResponse(
                {"detail": "Invalid password"},
                status_code=401,
            )
        return RedirectResponse(url="/login?error=invalid", status_code=302)

    token = create_access_token({"sub": str(admin.id)})
    if _wants_json(request):
        response: Response = JSONResponse({"status": "success"})
    else:
        response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key="admin_token", value=token, httponly=True)
    return response


@router.get("/logout")
async def logout() -> Response:
    response = RedirectResponse(url="/login")
    response.delete_cookie("admin_token")
    return response


@router.get("/change-password")
async def change_password_get(_: str = Depends(get_current_admin)) -> Response:
    return spa_index_response()


@router.post("/change-password")
async def change_password_post(
    request: Request,
    old_password: str | None = Form(default=None),
    new_password: str | None = Form(default=None),
    _: str = Depends(get_current_admin),
) -> Response:
    body = await _read_body(request)
    old_password_value = str(body.get("old_password") or old_password or "")
    new_password_value = str(body.get("new_password") or new_password or "")

    auth_service = get_admin_auth_service()
    if not await auth_service.verify_current_password(old_password_value):
        if _wants_json(request):
            return JSONResponse(
                {"detail": "Current password is incorrect"},
                status_code=400,
            )
        return RedirectResponse(url="/change-password?error=invalid", status_code=302)

    await auth_service.set_admin_password(new_password_value)
    if _wants_json(request):
        return JSONResponse({"status": "success"})
    return RedirectResponse(url="/change-password?status=success", status_code=302)

