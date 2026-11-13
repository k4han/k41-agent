from __future__ import annotations

import asyncio
import hmac
import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.delivery.http.github_webhook import router
from agent.modules import github


WEBHOOK_PATH = "/channels/github/webhook"
WEBHOOK_SECRET = "test-webhook-secret"
PAYLOAD = {
    "action": "opened",
    "repository": {"id": 100, "full_name": "octo/example"},
}


def signed_headers(body: bytes, *, event: str = "issues") -> dict[str, str]:
    signature = hmac.new(WEBHOOK_SECRET.encode(), body, sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": f"sha256={signature}",
    }


@pytest.fixture
def webhook_app(monkeypatch):
    settings = SimpleNamespace(enabled=True, webhook_secret=WEBHOOK_SECRET)
    monkeypatch.setattr(github, "get_github_settings", lambda: settings)
    service = SimpleNamespace(
        handle_webhook=AsyncMock(return_value={"status": "submitted", "task_id": "task-1"}),
    )
    app = FastAPI()
    app.state.github_automation_service = service
    app.include_router(router)
    return app, service, settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        "issues",
        "issue_comment",
        "pull_request_review_comment",
        "installation",
        "installation_repositories",
        "ping",
    ],
)
async def test_webhook_sends_response_before_processing_finishes(webhook_app, event):
    app, service, _ = webhook_app
    response_sent = asyncio.Event()
    processing_started = asyncio.Event()
    release_processing = asyncio.Event()
    messages = []

    async def slow_processing(**kwargs):
        processing_started.set()
        await release_processing.wait()
        return {"status": "submitted", "task_id": "task-1"}

    service.handle_webhook.side_effect = slow_processing
    body = json.dumps(PAYLOAD).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": WEBHOOK_PATH,
        "root_path": "",
        "query_string": b"",
        "headers": [
            (name.lower().encode(), value.encode())
            for name, value in signed_headers(body, event=event).items()
        ],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_sent.set()

    request_task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(response_sent.wait(), timeout=1)
        await asyncio.wait_for(processing_started.wait(), timeout=1)
        assert not request_task.done()
        assert messages[0]["status"] == 202
        assert json.loads(messages[1]["body"]) == {
            "status": "accepted",
            "delivery_id": "delivery-1",
        }
    finally:
        release_processing.set()
        await asyncio.wait_for(request_task, timeout=1)

    service.handle_webhook.assert_awaited_once_with(
        event=event,
        delivery_id="delivery-1",
        payload=PAYLOAD,
    )


@pytest.mark.parametrize("signature", [None, "sha256=invalid"])
def test_webhook_rejects_invalid_signature_without_processing(webhook_app, signature):
    app, service, _ = webhook_app
    body = json.dumps(PAYLOAD).encode()
    headers = signed_headers(body)
    if signature is None:
        del headers["X-Hub-Signature-256"]
    else:
        headers["X-Hub-Signature-256"] = signature

    with TestClient(app) as client:
        response = client.post(WEBHOOK_PATH, content=body, headers=headers)

    assert response.status_code == 401
    service.handle_webhook.assert_not_called()


@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b"42", b'"payload"'])
def test_webhook_rejects_invalid_payload_without_processing(webhook_app, body):
    app, service, _ = webhook_app
    with TestClient(app) as client:
        response = client.post(WEBHOOK_PATH, content=body, headers=signed_headers(body))

    assert response.status_code == 400
    service.handle_webhook.assert_not_called()


@pytest.mark.parametrize("setting, value", [("enabled", False), ("webhook_secret", "")])
def test_webhook_rejects_unavailable_configuration(webhook_app, setting, value):
    app, service, settings = webhook_app
    setattr(settings, setting, value)
    body = json.dumps(PAYLOAD).encode()
    with TestClient(app) as client:
        response = client.post(WEBHOOK_PATH, content=body, headers=signed_headers(body))

    assert response.status_code == 503
    service.handle_webhook.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [RuntimeError("clone failed"), HTTPException(status_code=502, detail="GitHub unavailable")],
)
def test_webhook_logs_background_failure_with_delivery_context(webhook_app, caplog, error):
    app, service, _ = webhook_app
    service.handle_webhook.side_effect = error
    body = json.dumps(PAYLOAD).encode()
    with TestClient(app) as client:
        response = client.post(WEBHOOK_PATH, content=body, headers=signed_headers(body))

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "delivery_id": "delivery-1"}
    service.handle_webhook.assert_awaited_once()
    assert "event=issues delivery=delivery-1 action=opened repo=octo/example" in caplog.text
    assert any(record.exc_info for record in caplog.records)
