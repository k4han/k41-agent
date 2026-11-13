"""CSRF (Cross-Site Request Forgery) protection middleware."""

from __future__ import annotations

import logging
import secrets

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

CSRF_TOKEN_LENGTH = 32
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FORM_FIELD_NAME = "csrf_token"


class CSRFProtectionMiddleware:
    """
    Middleware to protect against CSRF attacks for cookie-based authentication.

    How it works:
    1. Generates a random CSRF token and stores it in a cookie
    2. Requires the token to be sent back in request header or form data
    3. Validates token matches for state-changing methods (POST, PUT, PATCH, DELETE)
    4. Bypasses CSRF check for requests using Bearer token authentication (stateless)
    """

    def __init__(
        self,
        app: ASGIApp,
        enabled: bool = True,
        safe_methods: set[str] | None = None,
        exempt_paths: set[str] | None = None,
    ):
        self.app = app
        self.enabled = enabled
        self.safe_methods = safe_methods or {"GET", "HEAD", "OPTIONS", "TRACE"}
        self.exempt_paths = exempt_paths or {
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
        }

    def _is_exempt(self, path: str) -> bool:
        """Check if path is exempt from CSRF protection."""
        return any(path.startswith(exempt) for exempt in self.exempt_paths)

    def _get_csrf_token_from_request(self, request: Request) -> str | None:
        """Extract CSRF token from request headers or form data."""
        # Check header first
        token = request.headers.get(CSRF_HEADER_NAME)
        if token:
            return token.strip()

        # For form submissions, check form data
        # Note: This only works if form data hasn't been consumed yet
        # In practice, we'll primarily use header-based CSRF for API calls
        return None

    def _generate_csrf_token(self) -> str:
        """Generate a new cryptographically secure CSRF token."""
        return secrets.token_urlsafe(CSRF_TOKEN_LENGTH)

    def _get_or_create_csrf_token(self, request: Request) -> tuple[str, bool]:
        """
        Get existing CSRF token from cookie or create a new one.

        Returns:
            (token, is_new) - token string and whether it was newly created
        """
        existing_token = request.cookies.get(CSRF_COOKIE_NAME)
        if existing_token and len(existing_token) > 10:  # Basic sanity check
            return existing_token.strip(), False

        return self._generate_csrf_token(), True

    def _is_using_bearer_auth(self, request: Request) -> bool:
        """Check if request uses Bearer token authentication (stateless)."""
        auth_header = request.headers.get("Authorization", "")
        return auth_header.startswith("Bearer ")

    def _create_csrf_error_response(self, message: str) -> JSONResponse:
        """Create 403 Forbidden response for CSRF validation failure."""
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": message,
                "error": "csrf_validation_failed",
            },
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Validate CSRF token for state-changing requests."""
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        # Skip CSRF check for safe methods
        if request.method in self.safe_methods:
            csrf_token, is_new = self._get_or_create_csrf_token(request)
            cookie_headers = []
            if is_new:
                response = Response()
                response.set_cookie(
                    key=CSRF_COOKIE_NAME,
                    value=csrf_token,
                    httponly=True,
                    samesite="lax",
                    secure=request.url.scheme == "https",
                )
                cookie_headers = [header for header in response.raw_headers if header[0] == b"set-cookie"]

            async def send_response(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers[CSRF_HEADER_NAME] = csrf_token
                    headers.raw.extend(cookie_headers)
                await send(message)

            await self.app(scope, receive, send_response)
            return

        # Skip CSRF check for exempt paths
        if self._is_exempt(request.url.path):
            await self.app(scope, receive, send)
            return

        # Skip CSRF check for Bearer token authentication (stateless API)
        if self._is_using_bearer_auth(request):
            await self.app(scope, receive, send)
            return

        # For state-changing methods with cookie auth, validate CSRF token
        expected_token = request.cookies.get(CSRF_COOKIE_NAME)
        if not expected_token:
            logger.warning(
                "CSRF validation failed: no token in cookie for %s %s",
                request.method,
                request.url.path,
                extra={"method": request.method, "path": request.url.path},
            )
            response = self._create_csrf_error_response(
                "CSRF token missing. Please refresh the page and try again."
            )
            await response(scope, receive, send)
            return

        provided_token = self._get_csrf_token_from_request(request)
        if not provided_token:
            logger.warning(
                "CSRF validation failed: no token provided in request for %s %s",
                request.method,
                request.url.path,
                extra={"method": request.method, "path": request.url.path},
            )
            response = self._create_csrf_error_response(
                f"CSRF token required. Please include {CSRF_HEADER_NAME} header."
            )
            await response(scope, receive, send)
            return

        # Constant-time comparison to prevent timing attacks
        if not secrets.compare_digest(expected_token, provided_token):
            logger.warning(
                "CSRF validation failed: token mismatch for %s %s",
                request.method,
                request.url.path,
                extra={"method": request.method, "path": request.url.path},
            )
            response = self._create_csrf_error_response(
                "CSRF token validation failed. Please refresh the page and try again."
            )
            await response(scope, receive, send)
            return

        # CSRF validation passed
        await self.app(scope, receive, send)


def get_csrf_token_from_request(request: Request) -> str | None:
    """
    Helper function to get CSRF token from request cookie.

    Can be used in templates to embed token in forms.
    """
    return request.cookies.get(CSRF_COOKIE_NAME)
