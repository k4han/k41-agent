from fastapi import APIRouter, Depends, Form, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agent.modules.admin_auth import (
    create_access_token,
    get_admin_auth_service,
    get_current_admin,
)

router = APIRouter(tags=["auth"])
# Note: Provide appropriate path for templates
templates = Jinja2Templates(directory="agent/delivery/http/dashboard/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@router.post("/login")
async def login_post(request: Request, password: str = Form(...)):
    auth_service = get_admin_auth_service()
    admin = await auth_service.authenticate(password)
    if admin is None:
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Invalid password"})

    token = create_access_token({"sub": str(admin.id)})
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key="admin_token", value=token, httponly=True)
    return response

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("admin_token")
    return response

@router.get("/change-password", response_class=HTMLResponse)
async def change_password_get(request: Request, _ = Depends(get_current_admin)):
    return templates.TemplateResponse(request=request, name="change_password.html")

@router.post("/change-password")
async def change_password_post(
    request: Request, 
    old_password: str = Form(...), 
    new_password: str = Form(...),
    _ = Depends(get_current_admin)
):
    auth_service = get_admin_auth_service()

    if not await auth_service.verify_current_password(old_password):
        return templates.TemplateResponse(request=request, name="change_password.html", context={"error": "Mật khẩu cũ không chính xác"})

    await auth_service.set_admin_password(new_password)
    return templates.TemplateResponse(request=request, name="change_password.html", context={"success": "Đổi mật khẩu thành công"})
