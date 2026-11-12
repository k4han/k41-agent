from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope

from agent.delivery.http.dashboard.spa import spa_index_response
from agent.shared.infrastructure.http_routing import API_PATH_PREFIXES, accepts_html


RESERVED_PATHS = (
    *API_PATH_PREFIXES,
    "/dashboard-assets",
    "/integrations",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/ready",
    "/services",
    "/providers",
    "/prompt-variables",
    "/settings/sources",
    "/login",
    "/logout",
    "/favicon.ico",
    "/favicon-yellow.ico",
)
# These namespaces also have a legacy SPA page at their exact base path.
RESERVED_CHILD_PATHS = ("/channels/", "/agents/", "/scheduler/", "/tasks/")


class SPAFallbackRoute(APIRoute):
    """Match GET navigation with an explicit Accept: text/html media type.

    Wildcard or missing Accept headers keep normal routing errors (404/405),
    rather than serving HTML or triggering dashboard authentication redirects.
    Register this fallback after all concrete endpoints and static mounts.
    Reserved namespaces keep their routing errors even for HTML requests.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if match is Match.NONE:
            return match, child_scope

        request = Request(scope)
        path = f"/{child_scope['path_params']['path']}"
        # Reject before dependencies run so later API routes and mounts still match.
        if (
            request.method != "GET"
            or not accepts_html(request)
            or path.startswith(RESERVED_CHILD_PATHS)
            or any(path == base or path.startswith(f"{base}/") for base in RESERVED_PATHS)
        ):
            return Match.NONE, {}
        return match, child_scope


router = APIRouter(route_class=SPAFallbackRoute)


@router.get("/{path:path}", include_in_schema=False)
async def dashboard_spa() -> Response:
    return spa_index_response()
