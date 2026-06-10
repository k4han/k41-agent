from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent.shared.infrastructure.errors import classify_agent_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ErrorResponse:
    status_code: int
    code: str
    detail: str | dict[str, Any] | list[Any]
    headers: dict[str, str] | None = None
    errors: list[dict[str, Any]] | None = None


def status_code_from_exception(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def _code_from_status(status_code: int) -> str:
    if status_code == 400:
        return "bad_request"
    if status_code == 401:
        return "unauthorized"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code == 422:
        return "validation_error"
    if status_code == 429:
        return "rate_limit"
    if status_code == 504:
        return "timeout"
    if 500 <= status_code < 600:
        return "internal_error"
    return "http_error"


def _code_from_type(exc: BaseException) -> str:
    if isinstance(exc, ValueError):
        return "bad_request"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    if isinstance(exc, PermissionError):
        return "forbidden"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    return _code_from_status(status_code_from_exception(exc) or 500)


def _validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [
        {
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg"),
            "type": error.get("type"),
        }
        for error in exc.errors()
    ]


def _http_exception_response(exc: HTTPException) -> ErrorResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        return ErrorResponse(
            status_code=exc.status_code,
            code=str(detail.get("code") or _code_from_status(exc.status_code)),
            detail=detail,
            headers=exc.headers,
        )
    return ErrorResponse(
        status_code=exc.status_code,
        code=_code_from_status(exc.status_code),
        detail=detail,
        headers=exc.headers,
    )


def _generic_exception_response(exc: BaseException) -> ErrorResponse:
    status_code = status_code_from_exception(exc)
    if status_code is None:
        if isinstance(exc, ValueError):
            status_code = 400
        elif isinstance(exc, FileNotFoundError):
            status_code = 404
        elif isinstance(exc, PermissionError):
            status_code = 403
        elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            status_code = 504
        else:
            status_code = 500

    if 400 <= status_code < 500:
        return ErrorResponse(
            status_code=status_code,
            code=_code_from_type(exc),
            detail=str(exc) or _code_from_type(exc).replace("_", " "),
        )

    agent_error = classify_agent_error(exc)
    return ErrorResponse(
        status_code=status_code,
        code=agent_error.code,
        detail=agent_error.message,
    )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    return value


def _payload(error: ErrorResponse) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": error.code,
        "detail": _to_jsonable(error.detail),
    }
    if error.errors is not None:
        payload["errors"] = error.errors
    return payload


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    error = _http_exception_response(exc)
    return JSONResponse(
        status_code=error.status_code,
        content=_payload(error),
        headers=error.headers,
    )


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    error = ErrorResponse(
        status_code=422,
        code="validation_error",
        detail="Request validation failed.",
        errors=_validation_errors(exc),
    )
    return JSONResponse(status_code=422, content=_payload(error))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled HTTP exception for %s", request.url.path)
    error = _generic_exception_response(exc)
    return JSONResponse(status_code=error.status_code, content=_payload(error))


class HTTPExceptionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:
                raise

            request = Request(scope, receive, send_wrapper)
            response = await unhandled_exception_handler(request, exc)
            await response(scope, receive, send_wrapper)


def register_http_exception_handlers(app: FastAPI) -> None:
    if getattr(app.state, "_http_exception_handlers_registered", False):
        return

    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_middleware(HTTPExceptionMiddleware)
    app.state._http_exception_handlers_registered = True


__all__ = [
    "ErrorResponse",
    "register_http_exception_handlers",
    "status_code_from_exception",
]
