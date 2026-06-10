"""Structured logging middleware for HTTP requests and responses."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


class HTTPLoggingMiddleware(BaseHTTPMiddleware):
    """Logs structured information about every HTTP request and response.

    Captures:
    - method, path, query parameters
    - client IP (via X-Forwarded-For or direct)
    - response status code
    - request duration in milliseconds
    - user agent
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", "")
        client_ip = _get_client_ip(request)
        method = request.method
        path = request.url.path
        query = str(request.query_params) if request.query_params else ""
        user_agent = request.headers.get("User-Agent", "")

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        log_data: dict[str, Any] = {
            "method": method,
            "path": path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
            "user_agent": user_agent,
        }
        if query:
            log_data["query"] = query
        if request_id:
            log_data["request_id"] = request_id

        if response.status_code >= 500:
            logger.error("HTTP request completed", extra=log_data)
        elif response.status_code >= 400:
            logger.warning("HTTP request completed", extra=log_data)
        else:
            logger.info("HTTP request completed", extra=log_data)

        return response


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""
