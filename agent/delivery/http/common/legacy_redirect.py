import logging

from fastapi import Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

LEGACY_API_PREFIX = "/api/"
VERSIONED_API_PREFIX = "/v1/api/"


async def redirect_legacy_api(request: Request, call_next):
    """Redirect legacy /api/* requests to the versioned /v1/api/* endpoints."""
    path = request.url.path
    if path.startswith(LEGACY_API_PREFIX) and not path.startswith(VERSIONED_API_PREFIX):
        new_path = f"/v1{path}"
        logger.warning(
            "Legacy API endpoint accessed: %s - please update to: %s",
            path,
            new_path,
        )
        if request.url.query:
            new_path = f"{new_path}?{request.url.query}"
        return RedirectResponse(url=new_path, status_code=307)
    return await call_next(request)
