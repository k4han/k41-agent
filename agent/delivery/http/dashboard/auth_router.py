from fastapi import APIRouter, Depends, Form, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agent.modules.users.application.auth import create_access_token, get_current_admin
from agent.modules.users.application.pairing_handler import get_user_service

router = APIRouter(tags=["auth"])
# Note: Provide appropriate path for templates
templates = Jinja2Templates(directory="agent/delivery/http/dashboard/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@router.post("/login")
async def login_post(request: Request, password: str = Form(...)):
    user_service = get_user_service()
    admin = await user_service.get_admin_user()
    if not admin or not admin.password_hash or not user_service.verify_password(password, admin.password_hash):
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
    user_service = get_user_service()
    admin = await user_service.get_admin_user()
    
    if not admin or not admin.password_hash or not user_service.verify_password(old_password, admin.password_hash):
        return templates.TemplateResponse(request=request, name="change_password.html", context={"error": "Mật khẩu cũ không chính xác"})

    success = await user_service.update_admin_password(new_password)
    if success:
        return templates.TemplateResponse(request=request, name="change_password.html", context={"success": "Đổi mật khẩu thành công"})
    
    return templates.TemplateResponse(request=request, name="change_password.html", context={"error": "Có lỗi xảy ra, không thể đổi mật khẩu"})
