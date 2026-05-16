from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from agent.modules.github import (
    get_github_automation_service,
    get_github_settings,
    verify_webhook_signature,
)

router = APIRouter(tags=["github"])
logger = logging.getLogger(__name__)


@router.post("/channels/github/webhook")
async def github_webhook(
    request: Request,
    event: str | None = Header(default=None, alias="X-GitHub-Event"),
    delivery_id: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    settings = get_github_settings()
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="GitHub webhook is disabled.")
    if not settings.webhook_secret:
        raise HTTPException(status_code=503, detail="GitHub webhook secret is not configured.")

    body = await request.body()
    if not verify_webhook_signature(
        secret=settings.webhook_secret,
        body=body,
        signature_header=signature,
    ):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature.")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid GitHub webhook payload.") from exc

    try:
        service = get_github_automation_service()
        return await service.handle_webhook(
            event=event or "",
            delivery_id=delivery_id or "",
            payload=payload,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("GitHub webhook processing failed.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
