from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.identities import serialize_identity
from agent.delivery.http.dashboard.routes.helpers.settings import update_config_settings
from agent.modules.admin_auth import get_current_admin
from agent.modules.conversations import get_conversation_thread
from agent.modules.notifications.interruption import active_manager, identities_query
from agent.modules.notifications.settings import NotificationSettings, load_settings
from agent.modules.users.models import UserIdentity
from agent.shared.infrastructure.db import get_async_session
from agent.shared.thread_ids import resolve_thread_id

router = APIRouter(prefix="/dashboard-api")


@router.get("/notifications")
async def get_notifications(request: Request):
    config = get_request_config_service(request)
    manager = active_manager()
    async with await get_async_session() as session:
        identities = (await session.scalars(identities_query().order_by(UserIdentity.platform, UserIdentity.id))).all()
    settings = manager.settings if manager else load_settings(config)
    available = {identity.id for identity in identities}
    settings = settings.model_copy(update={"recipient_identity_ids": [
        identity for identity in settings.recipient_identity_ids if identity in available
    ]})
    return {"settings": settings,
            "identities": [serialize_identity(identity) for identity in identities]}


@router.put("/notifications")
async def update_notifications(body: NotificationSettings, request: Request):
    if body.recipient_identity_ids:
        async with await get_async_session() as session:
            identities = (await session.scalars(identities_query().where(
                UserIdentity.id.in_(body.recipient_identity_ids)))).all()
        if len(identities) != len(body.recipient_identity_ids):
            raise HTTPException(422, "Select active paired accounts.")
    update_config_settings(get_request_config_service(request), {
        f"notifications.{key}": value for key, value in body.model_dump().items()
    }, require_writable=True)
    return {"settings": body}


class ChatPresence(BaseModel):
    thread_id: str = Field(min_length=1, max_length=512)
    tab_id: str = Field(min_length=1, max_length=64)
    visible: bool


@router.post("/chat-presence", status_code=204, response_class=Response)
async def chat_presence(body: ChatPresence, admin: str = Depends(get_current_admin)):
    manager = active_manager()
    if manager:
        thread_id = resolve_thread_id(body.thread_id)
        if body.visible:
            stream = await manager.container.chat_stream_manager.get_session(thread_id)
            if stream is None and await get_conversation_thread(thread_id) is None:
                raise HTTPException(404, "Conversation not found.")
        manager.mark_presence(f"admin:{admin}", thread_id, body.tab_id, body.visible)
    return Response(status_code=204)
