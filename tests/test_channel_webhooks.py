from __future__ import annotations

import asyncio
import importlib
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.modules import channels


@pytest.fixture(params=["telegram", "zalo"])
def webhook(request, monkeypatch):
    channel = request.param
    module = importlib.import_module(f"agent.delivery.http.{channel}_webhook")
    processor = AsyncMock()
    runtime = SimpleNamespace(
        secret="secret", bot=object(), dispatcher=SimpleNamespace(feed_update=processor),
    )
    monkeypatch.setattr(module, "get_channel_webhook_runtime", lambda name: runtime)
    monkeypatch.setattr(
        module, "get_config_service", lambda: SimpleNamespace(get_str=lambda *args: "secret"),
    )
    if channel == "telegram":
        class FakeUpdate:
            @classmethod
            def model_validate(cls, payload, context):
                assert context == {"bot": runtime.bot}
                return SimpleNamespace(update_id=int(payload["update_id"]))

        types_module = ModuleType("aiogram.types")
        types_module.Update = FakeUpdate
        monkeypatch.setitem(sys.modules, "aiogram.types", types_module)
        payload = {"update_id": 99}
        header = module.TELEGRAM_WEBHOOK_SECRET_HEADER
    else:
        monkeypatch.setattr(channels, "handle_zalo_update", processor)
        payload = {"event_name": "message.text.received", "message": {"text": "hello"}}
        header = module.ZALO_WEBHOOK_SECRET_HEADER
    app = FastAPI()
    app.include_router(module.router)
    return SimpleNamespace(
        channel=channel, module=module, runtime=runtime, processor=processor,
        app=app, path=f"/channels/{channel}/webhook", payload=payload,
        headers={header: "secret"},
    )


@pytest.mark.asyncio
async def test_webhook_sends_acknowledgment_before_processing(webhook):
    response_sent = asyncio.Event()
    processing_started = asyncio.Event()
    release = asyncio.Event()
    messages = []

    async def slow_processing(*args, **kwargs):
        processing_started.set()
        assert response_sent.is_set()
        await release.wait()

    webhook.processor.side_effect = slow_processing
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": webhook.path,
        "root_path": "",
        "query_string": b"",
        "headers": [(name.lower().encode(), value.encode()) for name, value in webhook.headers.items()],
    }

    async def receive():
        return {"type": "http.request", "body": json.dumps(webhook.payload).encode(), "more_body": False}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_sent.set()

    request_task = asyncio.create_task(webhook.app(scope, receive, send))
    try:
        await asyncio.wait_for(response_sent.wait(), timeout=1)
        await asyncio.wait_for(processing_started.wait(), timeout=1)
        assert not request_task.done()
        assert messages[0]["status"] == 200
    finally:
        release.set()
        await asyncio.wait_for(request_task, timeout=1)

    webhook.processor.assert_awaited_once()
    if webhook.channel == "telegram":
        bot, update = webhook.processor.await_args.args
        assert bot is webhook.runtime.bot
        assert update.update_id == 99
    else:
        webhook.processor.assert_awaited_once_with(webhook.payload)


@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b"{}"])
def test_webhook_rejects_invalid_payload_without_processing(webhook, body):
    with TestClient(webhook.app) as client:
        response = client.post(webhook.path, content=body, headers=webhook.headers)
    assert response.status_code == 400
    webhook.processor.assert_not_called()


def test_webhook_rejects_invalid_secret_without_processing(webhook):
    headers = dict.fromkeys(webhook.headers, "wrong")
    with TestClient(webhook.app) as client:
        response = client.post(webhook.path, json=webhook.payload, headers=headers)
    assert response.status_code == 401
    webhook.processor.assert_not_called()


def test_webhook_rejects_inactive_runtime_without_processing(webhook, monkeypatch):
    monkeypatch.setattr(webhook.module, "get_channel_webhook_runtime", lambda name: None)
    with TestClient(webhook.app) as client:
        response = client.post(webhook.path, json=webhook.payload, headers=webhook.headers)
    assert response.status_code == 503
    webhook.processor.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("processing failed"), HTTPException(status_code=502)])
def test_webhook_logs_background_errors_after_acknowledgment(webhook, error, caplog):
    webhook.processor.side_effect = error
    with TestClient(webhook.app) as client:
        response = client.post(webhook.path, json=webhook.payload, headers=webhook.headers)
    assert response.status_code == 200
    webhook.processor.assert_awaited_once()
    assert any(record.exc_info for record in caplog.records)
    context = "update_id=99" if webhook.channel == "telegram" else "event=message.text.received"
    assert context in caplog.text


@pytest.mark.parametrize("wrapper", [{"ok": True}, {}])
@pytest.mark.parametrize("webhook", ["zalo"], indirect=True)
def test_zalo_webhook_unwraps_payload_before_background_processing(webhook, wrapper):
    with TestClient(webhook.app) as client:
        response = client.post(
            webhook.path, json={**wrapper, "result": webhook.payload}, headers=webhook.headers,
        )
    assert response.status_code == 200
    webhook.processor.assert_awaited_once_with(webhook.payload)
