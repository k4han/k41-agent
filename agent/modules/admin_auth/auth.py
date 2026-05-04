from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets

import jwt
from fastapi import HTTPException, Request, status

from agent.modules.admin_auth.service import get_admin_auth_service
from agent.shared.config import get_config_service

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

_cached_secret: str | None = None


def get_secret_key() -> str:
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret

    config = get_config_service()
    secret = config.get_str("security.jwt_secret")

    if not secret or secret == "dummy_secret_key":
        secret = secrets.token_hex(32)
        try:
            config.update_setting("security.jwt_secret", secret)
            logger.info("Generated new secure JWT secret and saved to config.")
        except Exception as exc:
            logger.warning("Failed to save generated JWT secret to config: %s", exc)

    _cached_secret = secret
    return _cached_secret


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, get_secret_key(), algorithm=ALGORITHM)


async def get_current_admin(request: Request) -> str:
    token = request.cookies.get("admin_token")
    if not token and "Authorization" in request.headers:
        auth_header = request.headers["Authorization"]
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        if request.url.path.startswith("/api/"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/login"})

    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        admin_id_str = str(payload.get("sub", "")).strip()
        if not admin_id_str:
            raise ValueError("Missing admin subject")

        admin_id = int(admin_id_str)
        service = get_admin_auth_service()
        admin = await service.get_admin_by_id(admin_id)
        if admin is None:
            raise ValueError("Admin not found")

    except Exception:
        if request.url.path.startswith("/api/"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/login"})

    return str(admin_id)
