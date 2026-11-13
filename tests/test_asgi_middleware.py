"""Middleware must preserve ASGI messages, task context, and cancellation."""

import asyncio
from contextvars import ContextVar

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from agent.delivery.http.common import LegacyAPIRedirectMiddleware
from agent.shared.infrastructure.csrf_protection import CSRFProtectionMiddleware
from agent.shared.infrastructure.http_logging import HTTPLoggingMiddleware


def http_scope(method="GET", headers=()):
    return {"type": "http", "asgi": {"spec_version": "2.4"}, "method": method,
            "path": "/stream", "scheme": "https", "server": ("testserver", 443),
            "query_string": b"", "headers": list(headers)}


@pytest.mark.asyncio
@pytest.mark.parametrize("middleware", [HTTPLoggingMiddleware, CSRFProtectionMiddleware, LegacyAPIRedirectMiddleware])
async def test_middleware_keeps_task_context_and_response_chunks(middleware):
    marker = ContextVar("marker", default="outer")
    task = asyncio.current_task()
    bodies = []
    received = []
    request_chunk = {"type": "http.request", "body": b"body", "more_body": False}

    async def receive():
        return request_chunk

    async def send(message):
        received.append(message)
        if message["type"] == "http.response.body":
            bodies.append(message["body"])

    async def app(scope, app_receive, app_send):
        assert asyncio.current_task() is task
        assert app_receive is receive
        assert await app_receive() is request_chunk
        marker.set("inner")
        await app_send({"type": "http.response.start", "status": 200, "headers": [(b"set-cookie", b"session=existing")]})
        for chunk in (b"first", b"second", b""):
            await app_send({"type": "http.response.body", "body": chunk, "more_body": bool(chunk)})

    await middleware(app)(http_scope(), receive, send)
    assert marker.get() == "inner"
    assert bodies == [b"first", b"second", b""]
    assert (b"set-cookie", b"session=existing") in received[0]["headers"]
    if middleware is CSRFProtectionMiddleware:
        assert len([h for h in received[0]["headers"] if h[0] == b"set-cookie"]) == 2
        assert any(b"Secure" in value and b"HttpOnly" in value for key, value in received[0]["headers"] if key == b"set-cookie")


@pytest.mark.asyncio
@pytest.mark.parametrize("middleware", [HTTPLoggingMiddleware, CSRFProtectionMiddleware, LegacyAPIRedirectMiddleware])
async def test_non_http_scopes_pass_through(middleware):
    scope = {"type": "websocket"}

    async def receive():
        return {"type": "websocket.disconnect"}

    async def send(message):
        pass

    async def app(app_scope, app_receive, app_send):
        assert (app_scope, app_receive, app_send) == (scope, receive, send)

    await middleware(app)(scope, receive, send)


@pytest.mark.asyncio
async def test_middleware_stack_propagates_cancellation_immediately():
    closed = []

    async def app(scope, receive, send):
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
        finally:
            closed.append(True)

    async def receive():
        raise AssertionError("Middleware must not consume the request")

    async def send(message):
        if message["type"] == "http.response.body":
            raise asyncio.CancelledError

    wrapped = HTTPLoggingMiddleware(CSRFProtectionMiddleware(LegacyAPIRedirectMiddleware(app)))
    with pytest.raises(asyncio.CancelledError):
        await wrapped(http_scope(), receive, send)
    assert closed


def test_csrf_validation_reuses_tokens_and_preserves_cookie_headers():
    app = FastAPI()
    app.add_middleware(CSRFProtectionMiddleware)

    @app.api_route("/change", methods=["GET", "POST"])
    async def change():
        response = Response("ok")
        response.set_cookie("session", "existing")
        return response

    with TestClient(app, base_url="https://testserver") as client:
        first = client.get("/change")
        token = first.headers["X-CSRF-Token"]
        second = client.get("/change")
        assert second.headers["X-CSRF-Token"] == token
        assert not any("csrf_token=" in value for value in second.headers.get_list("set-cookie"))
        assert client.post("/change").status_code == 403
        assert client.post("/change", headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.post("/change", headers={"X-CSRF-Token": token}).status_code == 200
        assert client.post("/change", headers={"Authorization": "Bearer token"}).status_code == 200
