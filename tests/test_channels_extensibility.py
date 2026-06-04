from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.modules.channels import (
    ChannelSettingField,
    ChannelSettingSection,
    InboundMessage,
    OutboundMessage,
    get_channel_registry,
)


class ReplyRecorder:
    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []

    async def __call__(self, message: OutboundMessage) -> object:
        self.messages.append(message)
        return object()


def inbound(text: str, reply: ReplyRecorder | None = None) -> InboundMessage:
    return InboundMessage(
        platform="testchat",
        user_id="user-1",
        channel_id="channel-1",
        text=text,
        is_private=True,
        reply=reply,
    )


def test_command_registry_parses_platform_command_suffix() -> None:
    from agent.modules.channels.commands import get_default_command_registry

    parsed = get_default_command_registry().parse("/code@mybot list files")

    assert parsed is not None
    assert parsed.name == "code"
    assert parsed.raw_args == "list files"
    assert parsed.args == ["list", "files"]


@pytest.mark.asyncio
async def test_public_command_bypasses_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.modules.channels import pipeline

    async def fail_auth(_: InboundMessage) -> bool:
        raise AssertionError("public command should not authenticate")

    monkeypatch.setattr(pipeline, "_authenticate", fail_auth)
    replies = ReplyRecorder()

    await pipeline.process_inbound_message(inbound("/help", replies))

    assert replies.messages
    assert "Commands:" in replies.messages[0].text


@pytest.mark.asyncio
async def test_unknown_command_replies_after_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.modules.channels import pipeline

    async def pass_auth(_: InboundMessage) -> bool:
        return True

    monkeypatch.setattr(pipeline, "_authenticate", pass_auth)
    replies = ReplyRecorder()

    await pipeline.process_inbound_message(inbound("/missing", replies))

    assert replies.messages[0].text.startswith("Unknown command: /missing")


@pytest.mark.asyncio
async def test_plain_message_routes_to_default_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.modules.channels import pipeline

    calls: list[InboundMessage] = []

    async def pass_auth(_: InboundMessage) -> bool:
        return True

    async def fake_stream(message: InboundMessage) -> None:
        calls.append(message)

    monkeypatch.setattr(pipeline, "_authenticate", pass_auth)
    monkeypatch.setattr(pipeline, "stream_default_agent_response", fake_stream)

    await pipeline.process_inbound_message(inbound("hello"))

    assert [call.text for call in calls] == ["hello"]


class FakeChannelAdapter:
    name = "fakechat"
    title = "Fake Chat"
    summary = "Fake chat adapter for tests."
    tagline = "Test platform"
    capabilities = frozenset({"chat", "outbound"})
    settings_sections = (
        ChannelSettingSection(id="authentication", title="Authentication"),
    )
    settings_schema = (
        ChannelSettingField(
            name="enabled",
            label="Fake Chat Enabled",
            input_type="boolean",
            section="authentication",
            default=True,
        ),
        ChannelSettingField(
            name="api_token",
            label="Fake Chat API Token",
            input_type="password",
            required=True,
            secret=True,
            section="authentication",
        ),
    )

    def __init__(self) -> None:
        self.sent: list[tuple[str, OutboundMessage]] = []

    def create_runner(self):
        async def runner() -> None:
            return None

        return runner

    async def send(self, destination: str, message: OutboundMessage) -> bool:
        self.sent.append((destination, message))
        return True

    async def test_connection(self) -> Any:
        return None

    async def sync_commands(self, commands) -> None:
        return None


@pytest.mark.asyncio
async def test_notification_delegates_to_registered_adapter() -> None:
    from agent.modules.notifications import send_notification

    adapter = FakeChannelAdapter()
    registry = get_channel_registry()
    registry.register(adapter, replace=True)
    try:
        ok = await send_notification("fakechat", "dest-1", "hello", mode="plain")
    finally:
        registry.unregister("fakechat")

    assert ok is True
    assert adapter.sent == [("dest-1", OutboundMessage(text="hello", mode="plain"))]


def test_dashboard_catalog_and_channels_include_adapter_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.delivery.http.dashboard.router import router
    from agent.delivery.http.dashboard.routes import dashboard as dashboard_routes
    from agent.modules.admin_auth import get_current_admin
    from agent.shared.config import ConfigService
    from agent.shared.config.default_source import DefaultConfigSource

    async def fake_paired_identities() -> list[dict[str, object]]:
        return []

    async def mock_admin(_: Request) -> str:
        return "admin"

    monkeypatch.setattr(dashboard_routes, "_paired_identities", fake_paired_identities)
    registry = get_channel_registry()
    registry.register(FakeChannelAdapter(), replace=True)
    try:
        app = FastAPI()
        app.include_router(router)
        app.state.config_service = ConfigService(sources=[DefaultConfigSource()])
        app.dependency_overrides[get_current_admin] = mock_admin
        client = TestClient(app)

        catalog = client.get("/dashboard-api/catalog").json()
        channels = client.get("/dashboard-api/channels").json()
    finally:
        registry.unregister("fakechat")

    assert any(item["name"] == "fakechat" for item in catalog["channels"])
    assert "fakechat" in channels["by_channel"]
    assert "channels.fakechat.api_token" in channels["settings"]
