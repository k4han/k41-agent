"""ACP protocol layer: JSON-RPC, process containment, and update translation."""

import asyncio
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import psutil
import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, ToolMessage

from agent.modules.acp.client import AcpClient
from agent.modules.acp.errors import AcpConnectionClosed, AcpRpcError
from agent.modules.acp.jsonrpc import JsonRpcConnection
from agent.modules.acp.process import AgentCommand, build_agent_env
from agent.modules.acp.translator import ACP_LIVE_KWARG, TurnTranscript

FAKE_AGENT = Path(__file__).parent / "fixtures" / "acp" / "fake_agent.py"


def fake_command(state: Path, *flags: str) -> AgentCommand:
    return AgentCommand(argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(state), *flags], cwd=state)


@pytest_asyncio.fixture
async def started_client(tmp_path):
    client = AcpClient(fake_command(tmp_path / "state"))
    await client.initialize()
    yield client
    await asyncio.to_thread(client.stop)


class Recorder:
    def __init__(self, client: AcpClient):
        self.client = client
        self.updates: list[dict] = []
        self.requests: list[tuple] = []
        self.answer: str | None = None

    def on_update(self, update):
        self.updates.append(update)

    def on_permission_request(self, rpc_id, params):
        self.requests.append((rpc_id, params))
        if self.answer:
            self.client.select_option(rpc_id, self.answer)

    def texts(self):
        return "".join(u["content"]["text"] for u in self.updates if u["sessionUpdate"] == "agent_message_chunk")


async def run_prompt(client, session_id, text):
    return await asyncio.wait_for(asyncio.wrap_future(client.prompt(session_id, [{"type": "text", "text": text}])), 20)


@pytest.mark.asyncio
async def test_handshake_and_text_turn(started_client):
    client = started_client
    assert client.supports_resume() and client.supports_load() and client.supports_system_prompt_append()
    assert [method["id"] for method in client.auth_methods] == ["oauth-personal", "gemini-api-key"]
    session = await client.new_session(str(Path.cwd()))
    recorder = Recorder(client)
    client.subscribe(session["sessionId"], recorder)
    result = await run_prompt(client, session["sessionId"], "scenario:text")
    assert result["stopReason"] == "end_turn" and recorder.texts() == "Hello world"


@pytest.mark.asyncio
async def test_rpc_error_carries_code(started_client):
    with pytest.raises(AcpRpcError) as error:
        await started_client.resume_session("missing", str(Path.cwd()))
    assert error.value.code == -32002 and not error.value.auth_required


@pytest.mark.asyncio
async def test_non_json_stdout_is_ignored(started_client):
    session = await started_client.new_session(str(Path.cwd()))
    recorder = Recorder(started_client)
    started_client.subscribe(session["sessionId"], recorder)
    await run_prompt(started_client, session["sessionId"], "scenario:noise")
    assert recorder.texts() == "Clean."


@pytest.mark.asyncio
async def test_non_spec_stop_reason_is_returned_unchanged(started_client):
    session = await started_client.new_session(str(Path.cwd()))
    started_client.subscribe(session["sessionId"], Recorder(started_client))
    assert (await run_prompt(started_client, session["sessionId"], "scenario:bad_stop"))["stopReason"] == "error"


@pytest.mark.asyncio
async def test_unknown_client_method_gets_method_not_found(started_client):
    session = await started_client.new_session(str(Path.cwd()))
    recorder = Recorder(started_client)
    started_client.subscribe(session["sessionId"], recorder)
    await run_prompt(started_client, session["sessionId"], "scenario:unknown_method")
    assert recorder.texts() == "code=-32601"


@pytest.mark.asyncio
async def test_permission_without_a_listener_is_cancelled(started_client):
    session = await started_client.new_session(str(Path.cwd()))
    result = await run_prompt(started_client, session["sessionId"], "scenario:permission")
    assert result["stopReason"] == "cancelled"


@pytest.mark.asyncio
async def test_crash_fails_pending_requests(tmp_path):
    closed = threading.Event()
    client = AcpClient(fake_command(tmp_path / "state"), on_closed=closed.set)
    try:
        await client.initialize()
        session = await client.new_session(str(tmp_path))
        client.subscribe(session["sessionId"], Recorder(client))
        with pytest.raises(AcpConnectionClosed):
            await run_prompt(client, session["sessionId"], "scenario:crash")
        assert closed.wait(5) and client.closed
    finally:
        await asyncio.to_thread(client.stop)


@pytest.mark.asyncio
async def test_works_on_selector_event_loop_regardless_of_policy(tmp_path):
    # The client never touches asyncio subprocess APIs, so a selector loop works
    # on Windows. This runs on one explicitly to protect that property.
    def scenario():
        async def body():
            client = AcpClient(fake_command(tmp_path / "state"))
            try:
                await client.initialize()
                return (await client.new_session(str(tmp_path)))["sessionId"]
            finally:
                await asyncio.to_thread(client.stop)

        loop = asyncio.SelectorEventLoop()
        try:
            return loop.run_until_complete(body())
        finally:
            loop.close()

    assert await asyncio.to_thread(scenario)


@pytest.mark.asyncio
async def test_stop_kills_a_server_that_ignores_stdin_eof(tmp_path):
    client = AcpClient(fake_command(tmp_path / "state"))
    await client.initialize()
    process = psutil.Process(client.process.pid)
    started = time.monotonic()
    await asyncio.to_thread(client.stop)
    assert time.monotonic() - started < 10
    assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE


@pytest.mark.skipif(os.name != "nt", reason="Job objects are Windows-only.")
@pytest.mark.asyncio
async def test_windows_job_does_not_run_the_descendant_tracker(tmp_path):
    client = AcpClient(fake_command(tmp_path / "state"))
    try:
        assert client.process.popen.coding_job.tracker is None
    finally:
        await asyncio.to_thread(client.stop)


def test_agent_env_keeps_explicit_vars_and_filters_ambient_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient")
    monkeypatch.setenv("GEMINI_API_KEY", "ambient")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test")
    env = build_agent_env({"GEMINI_HOME": "/data/home"})
    assert env["GEMINI_HOME"] == "/data/home" and env["HTTPS_PROXY"] == "http://proxy.test"
    assert "OPENAI_API_KEY" not in env and "GEMINI_API_KEY" not in env


def test_full_stdin_pipe_does_not_block_the_caller():
    read_fd, write_fd = os.pipe()
    stdout_read, stdout_write = os.pipe()
    connection = JsonRpcConnection(
        os.fdopen(stdout_read, "rb"), os.fdopen(write_fd, "wb"),
        on_notification=lambda *_: None, on_request=lambda *_: None,
    )
    connection.start()
    started = time.monotonic()
    connection.notify("huge", {"data": "x" * (8 * 1024 * 1024)})
    future = connection.request("after", {})
    assert time.monotonic() - started < 1
    with pytest.raises(FutureTimeout):
        future.result(timeout=0.05)
    os.close(read_fd)
    os.close(stdout_write)
    assert isinstance(future.exception(timeout=5), AcpConnectionClosed)


# Translator -----------------------------------------------------------------


def transcript() -> TurnTranscript:
    return TurnTranscript("turn-0001-aaaa", thread_id="thread")


def text_update(text, **extra):
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}, **extra}


def test_text_chunks_merge_into_one_message_and_stream_individually():
    record = transcript()
    assert record.apply(text_update("Hel")) == [{"type": "message", "content": "Hel"}]
    record.apply(text_update("lo"))
    messages = record.messages()
    assert len(messages) == 1 and messages[0].content == "Hello"
    assert messages[0].additional_kwargs[ACP_LIVE_KWARG] is True


def test_changed_message_id_starts_a_new_message():
    record = transcript()
    record.apply(text_update("a", messageId="1"))
    record.apply(text_update("b", messageId="2"))
    assert [m.content for m in record.messages()] == ["a", "b"]


def test_repeated_commits_keep_ids_stable_and_in_order():
    record = transcript()
    record.apply(text_update("a"))
    first = [m.id for m in record.messages()]
    record.apply(text_update("b"))
    assert [m.id for m in record.messages()] == first


def test_pending_tool_is_not_announced_until_it_runs():
    record = transcript()
    pending = {"sessionUpdate": "tool_call", "toolCallId": "x", "title": "echo", "kind": "execute", "status": "pending"}
    assert record.apply(pending) == []
    assert record.messages() == []
    running = record.apply({"sessionUpdate": "tool_call_update", "toolCallId": "x", "status": "in_progress", "rawInput": {"cmd": "echo"}})
    assert [event["type"] for event in running] == ["tool_call"]
    assert running[0]["name"] == "acp_execute" and running[0]["args"]["input"] == {"cmd": "echo"}
    done = record.apply({"sessionUpdate": "tool_call_update", "toolCallId": "x", "status": "completed", "rawOutput": {"combinedOutput": "out"}})
    assert done[0]["content"] == "out" and done[0]["tool_call_id"] == running[0]["id"]
    kinds = [type(m) for m in record.messages()]
    assert kinds == [AIMessage, ToolMessage]


def test_rejected_tool_is_shown_as_failed():
    record = transcript()
    record.apply({"sessionUpdate": "tool_call", "toolCallId": "x", "title": "rm", "kind": "execute", "status": "pending"})
    events = record.apply({"sessionUpdate": "tool_call_update", "toolCallId": "x", "status": "failed", "rawOutput": "Rejected by user"})
    assert [event["type"] for event in events] == ["tool_call", "tool_result"]
    assert record.messages()[-1].status == "error"


def test_never_executed_noise_is_dropped():
    record = transcript()
    record.apply({"sessionUpdate": "tool_call", "toolCallId": "x", "title": "Run create_file?", "kind": "edit", "status": "pending"})
    assert record.apply({"sessionUpdate": "tool_call_update", "toolCallId": "x", "status": "failed",
                         "rawOutput": "Tool call was approved but never executed."}) == []
    assert record.messages() == []


def test_cancel_notice_chunk_is_hidden_only_after_cancel():
    record = transcript()
    assert record.apply(text_update("The request was cancelled by the client."))
    record = transcript()
    record.cancel_requested = True
    assert record.apply(text_update("The request was cancelled by the client.")) == []


def test_tool_ids_are_scoped_to_the_turn():
    first, second = TurnTranscript("aaaaaaaa-1"), TurnTranscript("bbbbbbbb-1")
    update = {"sessionUpdate": "tool_call", "toolCallId": "s:1", "title": "x", "kind": "read", "status": "in_progress"}
    assert first.apply(update)[0]["id"] != second.apply(dict(update))[0]["id"]


def test_large_tool_values_are_bounded():
    record = transcript()
    record.apply({"sessionUpdate": "tool_call", "toolCallId": "x", "title": "t", "kind": "read", "status": "in_progress", "rawInput": {"blob": "y" * 100000}})
    events = record.apply({"sessionUpdate": "tool_call_update", "toolCallId": "x", "status": "completed", "rawOutput": "z" * 100000})
    assert len(events[0]["content"]) < 40000
    assert len(json.dumps(record.messages()[0].tool_calls[0]["args"])) < 20000


def test_usage_update_maps_to_reported_context_usage():
    event = transcript().apply({"sessionUpdate": "usage_update", "used": 5, "size": 10})[0]
    assert event["type"] == "context_usage" and event["estimated"] is False
    assert event["current_context_tokens"] == 5 and event["context_window"] == 10
    assert transcript().apply({"sessionUpdate": "usage_update", "used": "5", "size": 10}) == []


def test_unknown_updates_are_ignored():
    assert transcript().apply({"sessionUpdate": "something_new", "x": 1}) == []


def test_fixtures_hold_no_credentials():
    pattern = re.compile(r"ya29\.|1//[A-Za-z0-9_-]{10}|AIza[0-9A-Za-z_-]{10}|eyJ[A-Za-z0-9_-]{10}|[\w.+-]+@[\w-]+\.[a-z]{2,}")
    for path in FAKE_AGENT.parent.rglob("*"):
        if path.is_file() and path.suffix != ".pyc":
            assert not pattern.search(path.read_text(encoding="utf-8")), path
