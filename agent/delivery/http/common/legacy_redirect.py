import logging

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

LEGACY_API_PREFIX = "/api/"
VERSIONED_API_PREFIX = "/v1/api/"


def _redirect_response(request: Request) -> RedirectResponse | None:
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
    return None


class LegacyAPIRedirectMiddleware:
    """Redirect legacy API paths without wrapping other response streams."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            response = _redirect_response(Request(scope))
            if response is not None:
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def redirect_legacy_api(request: Request, call_next):
    """Compatibility adapter for callers using the HTTP middleware decorator."""
    response = _redirect_response(request)
    if response is not None:
        return response
    return await call_next(request)
