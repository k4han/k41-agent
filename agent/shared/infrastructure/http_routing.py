"""Shared HTTP path classification and content negotiation."""

from starlette.requests import Request


API_PATH_PREFIXES = ("/api", "/v1/api", "/v2/api", "/dashboard-api")


def is_api_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in API_PATH_PREFIXES)


def accepts_html(request: Request) -> bool:
    """Require an explicit HTML media type; wildcard or missing Accept is not HTML."""
    return "text/html" in request.headers.get("accept", "").lower()
