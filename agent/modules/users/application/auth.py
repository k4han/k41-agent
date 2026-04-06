import jwt
import secrets
import logging
from datetime import datetime, timedelta, timezone
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from agent.shared.config import get_config_service

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

def get_secret_key() -> str:
    config = get_config_service()
    secret = config.get_str("security.jwt_secret")
    
    if not secret or secret == "dummy_secret_key":
        secret = secrets.token_hex(32)
        try:
            config.update_setting("security.jwt_secret", secret)
            logger.info("Generated new secure JWT secret and saved to config.")
        except Exception as e:
            logger.warning(f"Failed to save generated JWT secret to config: {e}")
            
    return secret

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, get_secret_key(), algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_admin(request: Request):
    token = request.cookies.get("admin_token")
    if not token and "Authorization" in request.headers:
        auth_header = request.headers["Authorization"]
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if not token:
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/login"})
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        admin_id: str = payload.get("sub")
        if admin_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    except Exception:
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/login"})
    return admin_id
