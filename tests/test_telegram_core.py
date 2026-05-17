import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class DummyUser:
    def __init__(self, user_id: int = 123) -> None:
        self.id = user_id


class DummyChat:
    def __init__(self, chat_id: int = 456, chat_type: str = "private") -> None:
        self.id = chat_id
        self.type = chat_type


class DummySentMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[str, str | None]] = []

    async def edit_text(self, text: str, parse_mode: str | None = None):
        self.edits.append((text, parse_mode))
        return self


class DummyMessage:
    def __init__(self, text: str, chat_type: str = "private") -> None:
        self.text = text
        self.from_user = DummyUser()
        self.chat = DummyChat(chat_type=chat_type)
        self.answers: list[tuple[str, str | None]] = []
        self.sent_messages: list[DummySentMessage] = []

    async def answer(self, text: str, parse_mode: str | None = None):
        self.answers.append((text, parse_mode))
        sent = DummySentMessage()
        self.sent_messages.append(sent)
        return sent


def test_telegram_formatter_escapes_markdown_and_chunks() -> None:
    from agent.modules.channels.telegram.formatter import (
        chunk_telegram_message,
        format_telegram_message,
    )

    formatted = format_telegram_message("**<tag>** and `x<y>`")
    assert formatted == "<b>&lt;tag&gt;</b> and <code>x&lt;y&gt;</code>"

    code_block = format_telegram_message("```python\nprint('<x>')\n```")
    assert '<pre><code class="language-python">' in code_block
    assert "&lt;x&gt;" in code_block

    chunks = chunk_telegram_message(("line\n\n" * 20).strip(), max_len=40)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 40 for chunk in chunks)


@pytest.mark.asyncio
async def test_telegram_sender_falls_back_to_plain_text(monkeypatch) -> None:
    from agent.modules.channels.telegram import sender

    def fail_format(_: str) -> str:
        raise ValueError("boom")

    monkeypatch.setattr(sender, "format_telegram_message", fail_format)
    sent: list[tuple[str, str | None]] = []

    async def send_text(chunk: str, parse_mode: str | None):
        sent.append((chunk, parse_mode))
        return chunk

    result = await sender.send_telegram_chunks(send_text, "raw <text>", mode="markdown")

    assert result == ["raw <text>"]
    assert sent == [("raw <text>", None)]


@pytest.mark.asyncio
async def test_telegram_sender_retries_html_send_as_plain_text() -> None:
    from agent.modules.channels.telegram.sender import send_telegram_chunks

    sent: list[tuple[str, str | None]] = []

    async def send_text(chunk: str, parse_mode: str | None):
        if parse_mode == "HTML":
            raise RuntimeError("bad html")
        sent.append((chunk, parse_mode))
        return chunk

    result = await send_telegram_chunks(send_text, "<b>Hello</b>", mode="html")

    assert result == ["Hello"]
    assert sent == [("Hello", None)]


@pytest.mark.asyncio
async def test_telegram_auth_middleware_allows_public_commands(monkeypatch) -> None:
    from agent.modules.channels.telegram import commands

    auth_calls = 0
    handler_calls = 0

    async def fake_auth(*args, **kwargs) -> bool:
        nonlocal auth_calls
        auth_calls += 1
        return False

    async def handler(event, data):
        nonlocal handler_calls
        handler_calls += 1
        return "handled"

    monkeypatch.setattr(commands, "authenticate_channel_message", fake_auth)

    result = await commands.auth_middleware(handler, DummyMessage("/help"), {})

    assert result == "handled"
    assert handler_calls == 1
    assert auth_calls == 0


@pytest.mark.asyncio
async def test_telegram_auth_middleware_handles_pair_before_agent(monkeypatch) -> None:
    from agent.modules.channels.telegram import commands

    auth_calls = 0
    handler_calls = 0

    async def fake_auth(*args, **kwargs) -> bool:
        nonlocal auth_calls
        auth_calls += 1
        return False

    async def handler(event, data):
        nonlocal handler_calls
        handler_calls += 1

    monkeypatch.setattr(commands, "authenticate_channel_message", fake_auth)

    result = await commands.auth_middleware(handler, DummyMessage("/pair ABCD"), {})

    assert result is None
    assert handler_calls == 0
    assert auth_calls == 1


@pytest.mark.asyncio
async def test_telegram_auth_middleware_ignores_non_private_chat(monkeypatch) -> None:
    from agent.modules.channels.telegram import commands

    auth_calls = 0
    handler_calls = 0

    async def fake_auth(*args, **kwargs) -> bool:
        nonlocal auth_calls
        auth_calls += 1
        return True

    async def handler(event, data):
        nonlocal handler_calls
        handler_calls += 1

    monkeypatch.setattr(commands, "authenticate_channel_message", fake_auth)

    result = await commands.auth_middleware(handler, DummyMessage("hello", "group"), {})

    assert result is None
    assert handler_calls == 0
    assert auth_calls == 0


def test_telegram_run_params_use_private_chat_context() -> None:
    from agent.modules.channels.telegram.commands import _build_telegram_run_params

    message = DummyMessage("hello")
    params = _build_telegram_run_params(message, "default")

    assert params["user_input"] == "hello"
    assert params["agent_name"] == "default"
    assert params["thread_id"] == "telegram_123_456"


@pytest.mark.asyncio
async def test_telegram_streaming_edits_status_and_sends_final(monkeypatch) -> None:
    import agent.modules.agent_runtime as runtime
    from agent.modules.channels.telegram.streaming import handle_streaming_response

    async def fake_run_agent_stream(**params):
        yield {"type": "tool_call", "name": "read_file", "args": {"path": "a.txt"}}
        yield {"type": "final", "content": "**Done**"}

    monkeypatch.setattr(runtime, "run_agent_stream", fake_run_agent_stream)

    message = DummyMessage("hello")
    await handle_streaming_response(message, {"user_input": "hello"})

    assert message.answers[0] == ("Processing...", None)
    status_message = message.sent_messages[0]
    assert status_message.edits[0][0].startswith("Processing...\n- read_file")
    assert status_message.edits[-1] == ("<b>Done</b>", "HTML")


@pytest.mark.asyncio
async def test_telegram_notification_uses_chunked_sender() -> None:
    from agent.modules.notifications.service import send_notification, set_telegram_bot
    from agent.modules.users import Platform

    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str | None]] = []

        async def send_message(self, chat_id: str, text: str, parse_mode: str | None = None):
            self.calls.append((chat_id, text, parse_mode))
            return text

    bot = FakeBot()
    set_telegram_bot(bot)
    try:
        ok = await send_notification(
            Platform.TELEGRAM,
            "123",
            "<b>Result</b>\n" + ("x" * 4100),
        )
    finally:
        set_telegram_bot(None)

    assert ok is True
    assert len(bot.calls) > 1
    assert all(call[0] == "123" for call in bot.calls)
    assert all(len(call[1]) <= 4000 for call in bot.calls)
    assert all(call[2] == "HTML" for call in bot.calls)


@pytest.mark.asyncio
async def test_telegram_notification_can_send_markdown() -> None:
    from agent.modules.notifications.service import send_notification, set_telegram_bot
    from agent.modules.users import Platform

    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str | None]] = []

        async def send_message(self, chat_id: str, text: str, parse_mode: str | None = None):
            self.calls.append((chat_id, text, parse_mode))
            return text

    bot = FakeBot()
    set_telegram_bot(bot)
    try:
        ok = await send_notification(
            Platform.TELEGRAM,
            "123",
            "**Result**\n`x<y>`",
            mode="markdown",
        )
    finally:
        set_telegram_bot(None)

    assert ok is True
    assert bot.calls == [
        ("123", "<b>Result</b>\n<code>x&lt;y&gt;</code>", "HTML")
    ]


def _create_webhook_client() -> TestClient:
    from agent.delivery.http.telegram_webhook import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_telegram_webhook_rejects_invalid_secret() -> None:
    from agent.modules.channels.telegram.bot import (
        TelegramWebhookRuntime,
        set_telegram_webhook_runtime,
    )

    class FakeDispatcher:
        async def feed_update(self, bot, update):
            return None

    set_telegram_webhook_runtime(
        TelegramWebhookRuntime(bot=object(), dispatcher=FakeDispatcher(), secret="secret")
    )
    try:
        response = _create_webhook_client().post(
            "/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 1},
        )
    finally:
        set_telegram_webhook_runtime(None)

    assert response.status_code == 401


def test_telegram_webhook_returns_503_when_runtime_inactive() -> None:
    from agent.modules.channels.telegram.bot import set_telegram_webhook_runtime

    set_telegram_webhook_runtime(None)

    response = _create_webhook_client().post(
        "/channels/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        json={"update_id": 1},
    )

    assert response.status_code == 503


def test_telegram_webhook_feeds_update() -> None:
    from agent.modules.channels.telegram.bot import (
        TelegramWebhookRuntime,
        set_telegram_webhook_runtime,
    )

    class FakeDispatcher:
        def __init__(self) -> None:
            self.calls = []

        async def feed_update(self, bot, update):
            self.calls.append((bot, update.update_id))
            return None

    dispatcher = FakeDispatcher()
    bot = object()
    set_telegram_webhook_runtime(
        TelegramWebhookRuntime(bot=bot, dispatcher=dispatcher, secret="secret")
    )
    try:
        response = _create_webhook_client().post(
            "/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            json={"update_id": 99},
        )
    finally:
        set_telegram_webhook_runtime(None)

    assert response.status_code == 204
    assert dispatcher.calls == [(bot, 99)]
