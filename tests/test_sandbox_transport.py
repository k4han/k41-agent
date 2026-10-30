"""Sandbox transport budgets and failure handling without cloud credentials."""

import asyncio
import base64
import io
import json
import shlex
import socket
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.modules.tools.coding.models import CodingError
from agent.modules.tools.coding.remote import BOOTSTRAP, SandboxClient, WORKER_CHECK_INTERVAL_SECONDS
from agent.modules.tools.coding.remote_worker import MAX_INLINE_RESPONSE_BYTES, rpc_output
from agent.modules.workspaces import CommandResult


@pytest.fixture
def transport(tmp_path, monkeypatch):
    calls = []
    responses = []
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    def dispatch(directory, request):
        responses.append(request)
        return {"result": request.get("values", {}), "worker_duration_seconds": 0.001}

    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.rpc_request", dispatch)

    class Backend:
        root = str(tmp_path)
        ref = SimpleNamespace(backend="test")

        async def upload_coding_file(self, content, path):
            calls.append("upload")
            # Bootstrap only needs a provider acknowledgement in this fixture.
            if not path.endswith(".zip"):
                Path(path).write_bytes(content)

        async def download_coding_file(self, path):
            calls.append("download")
            return Path(path).read_bytes()

        async def execute_coding_command(self, command, *, timeout):
            parts = shlex.split(command)
            if parts[2] == BOOTSTRAP:
                calls.append("bootstrap")
                return CommandResult(json.dumps({"runtime_dir": str(runtime), "python": "python3", "bundle": "runtime.zip"}), 0)
            if "rpc_output(Path" in parts[2]:
                calls.append("rpc")
                output = io.StringIO()
                with redirect_stdout(output):
                    rpc_output(Path(parts[4]), parts[5], Path(parts[6]), parts[7])
                return CommandResult(output.getvalue(), 0)
            calls.append("cleanup")
            Path(parts[3]).unlink(missing_ok=True)
            return CommandResult("", 0)

    client = SandboxClient(Backend(), b"bundle", "namespace", "generation")
    return client, calls, responses, runtime


@pytest.mark.asyncio
async def test_small_rpc_uses_one_backend_call_and_reuses_worker(transport):
    client, calls, requests, runtime = transport
    await asyncio.gather(*(client.ensure_started() for _ in range(4)))
    assert calls == ["upload", "bootstrap"]
    calls.clear()
    values = {"content": "quotes '\" $() ` ;\n Unicode: \u03bb"}
    for _ in range(2):
        await client.ensure_started()
        assert await client.call({"operation": "execute", "values": values}) == values
    assert calls == ["rpc", "rpc"]
    assert len(requests) == 2
    assert not list(runtime.iterdir())
    client.last_activity -= WORKER_CHECK_INTERVAL_SECONDS + 1
    await client.ensure_started()
    assert calls[-1] == "bootstrap" and calls.count("upload") == 0


@pytest.mark.asyncio
async def test_rpc_timings_exclude_arguments_and_contents(transport, caplog):
    client, _, _, _ = transport
    await client.ensure_started()
    with caplog.at_level("INFO", logger="agent.modules.tools.coding.remote"):
        await client.call({"operation": "execute", "values": {"content": "private-content"}})
    record = next(record for record in caplog.records if hasattr(record, "rpc_operation"))
    assert record.backend_calls == 1
    assert record.worker_duration_seconds == 0.001
    assert record.duration_seconds >= 0 and record.transport_duration_seconds >= 0
    assert "private-content" not in caplog.text


@pytest.mark.asyncio
async def test_secret_request_never_appears_in_transport_command_and_is_unlinked_before_dispatch(transport, monkeypatch):
    client, calls, _, runtime = transport
    await client.ensure_started()
    execute = client.backend.execute_coding_command
    commands = []

    async def capture(command, *, timeout):
        commands.append(command)
        assert "transport-private-token" not in command
        assert base64.b64encode(b"transport-private-token").decode() not in command
        return await execute(command, timeout=timeout)

    def dispatch(directory, request):
        assert request["execution_secrets"] == {"TOKEN": "transport-private-token"}
        assert not list(runtime.glob("*.secret-request"))
        return {"result": "executed"}

    monkeypatch.setattr(client.backend, "execute_coding_command", capture)
    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.rpc_request", dispatch)
    assert await client.call({"operation": "execute", "name": "bash", "execution_secrets": {"TOKEN": "transport-private-token"}}) == "executed"
    assert len(commands) == 2 and not list(runtime.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_secret_transport_cleans_uploaded_file_without_replay(transport, monkeypatch, cancel):
    client, _, _, runtime = transport
    await client.ensure_started()
    execute = client.backend.execute_coding_command
    attempts = []

    async def disconnected(command, *, timeout):
        if "rpc_output(Path" in command:
            attempts.append(command)
            assert list(runtime.glob("*.secret-request"))
            if cancel:
                raise asyncio.CancelledError
            return CommandResult("", 1)
        return await execute(command, timeout=timeout)

    monkeypatch.setattr(client.backend, "execute_coding_command", disconnected)
    with pytest.raises(asyncio.CancelledError if cancel else CodingError):
        await client.call({"operation": "execute", "execution_secrets": {"TOKEN": "private"}})
    assert len(attempts) == 1 and not list(runtime.iterdir())


@pytest.mark.asyncio
async def test_cancellation_waits_for_inflight_private_upload_before_cleanup(transport, monkeypatch):
    client, calls, _, runtime = transport
    await client.ensure_started()
    calls.clear()
    started, finish = asyncio.Event(), asyncio.Event()
    upload = client.backend.upload_coding_file

    async def slow_upload(content, path):
        started.set()
        await finish.wait()
        await upload(content, path)

    monkeypatch.setattr(client.backend, "upload_coding_file", slow_upload)
    task = asyncio.create_task(client.call({"operation": "execute", "execution_secrets": {"TOKEN": "private"}}))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ["upload", "cleanup"]
    assert not list(runtime.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [OSError, CodingError])
async def test_private_upload_errors_cannot_expose_request_values(transport, monkeypatch, error_type):
    client, _, _, runtime = transport
    await client.ensure_started()
    upload = client.backend.upload_coding_file

    async def failed_upload(content, path):
        await upload(content, path)
        if error_type is CodingError:
            raise CodingError("execution_error", "Provider rejected private-upload-token", request=content.decode())
        raise OSError("Provider rejected private-upload-token")

    monkeypatch.setattr(client.backend, "upload_coding_file", failed_upload)
    with pytest.raises(CodingError) as caught:
        await client.call({"operation": "execute", "execution_secrets": {"TOKEN": "private-upload-token"}})
    assert "private-upload-token" not in str(caught.value)
    assert caught.value.details == {}
    assert not list(runtime.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["execution_error", "invalid_input", "permission_denied", "runtime_unavailable"])
@pytest.mark.parametrize("spilled", [False, True])
async def test_secret_execution_preserves_structured_worker_errors(transport, monkeypatch, code, spilled):
    from agent.modules.tools.coding.remote_worker import failure

    client, calls, _, runtime = transport
    await client.ensure_started()
    calls.clear()
    details = {"operation": "spawn", "errno": 13}
    if spilled:
        details["diagnostic"] = "x" * MAX_INLINE_RESPONSE_BYTES
    message = "Cannot spawn the requested process."
    response = {"error": failure(CodingError(code, message, **details)).to_dict()}
    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.rpc_request", lambda *args: response)
    with pytest.raises(CodingError) as caught:
        await client.call({"operation": "execute", "execution_secrets": {"TOKEN": "private"}})
    assert caught.value.code == code
    assert str(caught.value) == message
    assert caught.value.details == details
    assert calls.count("rpc") == 1
    assert not list(runtime.iterdir())
    if code == "runtime_unavailable":
        assert client.last_activity == 0


@pytest.mark.asyncio
async def test_private_transport_cannot_spill_inside_the_project_workspace(transport):
    client, calls, _, _ = transport
    client.endpoint = {"runtime_dir": "/workspace/.runtime"}
    with pytest.raises(CodingError, match="outside the project workspace"):
        await client.call({"operation": "execute", "context": {"workspace": "/workspace"},
                           "execution_secrets": {"TOKEN": "private"}})
    assert calls == []


@pytest.mark.asyncio
async def test_large_request_and_response_spill_without_reexecution(transport):
    client, calls, requests, runtime = transport
    await client.ensure_started()
    calls.clear()
    values = {"content": "\u03bb" * 100000}
    assert await client.call({"operation": "execute", "values": values}) == values
    assert calls == ["upload", "rpc", "download", "cleanup"]
    assert len(requests) == 1
    assert not list(runtime.iterdir())


@pytest.mark.asyncio
async def test_large_request_small_response_needs_no_download_or_cleanup(transport, monkeypatch):
    client, calls, _, runtime = transport
    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.rpc_request", lambda *args: {"result": "written"})
    await client.ensure_started()
    calls.clear()
    assert await client.call({"operation": "execute", "values": {"content": "x" * 100000}}) == "written"
    assert calls == ["upload", "rpc"]
    assert not list(runtime.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("output,exit_code", [("truncated{", 0), ('{"response": []}', 0), ("", 1)])
async def test_lost_or_invalid_response_never_replays_mutation(transport, monkeypatch, output, exit_code):
    client, _, requests, _ = transport
    await client.ensure_started()
    attempts = []

    async def execute(command, *, timeout):
        attempts.append(command)
        return CommandResult(output, exit_code)

    monkeypatch.setattr(client.backend, "execute_coding_command", execute)
    with pytest.raises(CodingError) as caught:
        await client.call({"operation": "execute", "values": {"content": "mutation"}})
    assert caught.value.code == "unknown_outcome"
    assert len(attempts) == 1 and not requests


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_hide_success(transport, monkeypatch):
    client, _, _, _ = transport
    await client.ensure_started()
    execute = client.backend.execute_coding_command

    async def fail_cleanup(command, *, timeout):
        if "unlink" in command:
            raise OSError("Cleanup unavailable")
        return await execute(command, timeout=timeout)

    monkeypatch.setattr(client.backend, "execute_coding_command", fail_cleanup)
    values = {"content": "x" * 100000}
    assert await client.call({"operation": "execute", "values": values}) == values


@pytest.mark.asyncio
async def test_missing_worker_invalidates_health_without_replaying(transport, monkeypatch):
    from agent.modules.tools.coding.remote_worker import failure
    client, calls, _, _ = transport
    await client.ensure_started()
    calls.clear()
    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.rpc_request", lambda *args: {
        "error": failure(CodingError("runtime_unavailable", "No request was sent.")).to_dict()
    })
    with pytest.raises(CodingError) as caught:
        await client.call({"operation": "execute"})
    assert caught.value.code == "runtime_unavailable"
    assert calls == ["rpc"] and client.last_activity == 0
    await client.ensure_started()
    assert calls == ["rpc", "bootstrap"]


def test_rpc_output_bounds_stdout_and_removes_uploaded_requests(tmp_path, monkeypatch, capsys):
    request_path = tmp_path / "request"
    response_path = tmp_path / "response"
    request_path.write_text('{"operation": "execute"}')
    response = {"result": "\u03bb" * MAX_INLINE_RESPONSE_BYTES}
    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.rpc_request", lambda *args: response)
    rpc_output(tmp_path, str(request_path), response_path, "")
    output = capsys.readouterr().out
    assert len(output) < MAX_INLINE_RESPONSE_BYTES
    assert json.loads(output) == {"response_file": True}
    assert json.loads(response_path.read_bytes()) == response
    assert not request_path.exists()


@pytest.mark.parametrize("disconnected", [False, True])
def test_socket_protocol_authenticates_and_receives_partial_frames(tmp_path, monkeypatch, disconnected):
    from agent.modules.tools.coding.remote_worker import rpc_request
    (tmp_path / "token").write_text("private-token")
    response = json.dumps({"result": "complete"}).encode()
    chunks = bytearray(len(response).to_bytes(8, "big") + response)
    if disconnected:
        del chunks[-4:]
    sent = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            assert timeout == 120

        def connect(self, address):
            assert address == str(tmp_path / "worker.sock")

        def sendall(self, data):
            sent.append(data)

        def recv(self, size):
            data = bytes(chunks[:min(size, 3)])
            del chunks[:len(data)]
            return data

    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.socket.socket", lambda *args: Connection())
    monkeypatch.setattr("agent.modules.tools.coding.remote_worker.socket.AF_UNIX", 1, raising=False)
    if disconnected:
        with pytest.raises(ConnectionError, match="outcome must be inspected"):
            rpc_request(tmp_path, {"operation": "execute"})
    else:
        assert rpc_request(tmp_path, {"operation": "execute"}) == {"result": "complete"}
    assert len(sent) == 1
    assert int.from_bytes(sent[0][:8], "big") == len(sent[0][8:])
    assert json.loads(sent[0][8:])["token"] == "private-token"


@pytest.mark.asyncio
async def test_worker_server_dispatch_and_timings_over_real_socket(tmp_path, monkeypatch):
    """Use a loopback adapter so the Unix RPC framing also runs on Windows."""
    from agent.modules.tools.coding import remote_worker
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "token").write_text("private-token")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ready = asyncio.Event()
    address = None

    async def start_server(handle, *, path):
        nonlocal address
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        address = server.sockets[0].getsockname()
        Path(path).touch()
        ready.set()
        return server

    class Connection:
        def __init__(self, *args):
            self.connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def connect(self, path):
            self.connection.connect(address)

    monkeypatch.setattr(remote_worker.asyncio, "start_unix_server", start_server, raising=False)
    monkeypatch.setattr(remote_worker, "socket", SimpleNamespace(socket=Connection, AF_UNIX=1, SOCK_STREAM=1))
    task = asyncio.create_task(remote_worker.serve(runtime, tmp_path / "outputs", str(workspace)))

    async def call(request):
        return await asyncio.to_thread(remote_worker.rpc_request, runtime, request)

    try:
        await asyncio.wait_for(ready.wait(), 5)
        request = {"operation": "prepare", "name": "write", "values": {"path": "file.txt", "content": "written"},
                   "context": {"agent_name": "default", "workspace": str(workspace), "thread_id": "thread"}}
        prepared = await call({"operation": "prepare_batch", "requests": [request]})
        assert prepared["worker_timings"]["preparation_duration_seconds"] >= 0
        permissions = prepared["result"]["results"][0]["permissions"]
        executed = await call({**request, "operation": "execute", "authorized": [
            remote_worker.permission_identity(item["action"], item["resource"], item["metadata"])
            for item in permissions
        ]})
        assert executed["result"]["status"] == "success"
        assert (workspace / "file.txt").read_text() == "written"
        timings = executed["worker_timings"]
        assert timings["preparation_duration_seconds"] >= 0
        assert timings["execution_duration_seconds"] >= 0
        assert sum(timings.values()) <= executed["worker_duration_seconds"]
        denied = await call({**request, "operation": "execute", "authorized": []})
        assert denied["error"]["error"]["code"] == "permission_denied"
        assert "worker_duration_seconds" in denied
        assert (await call({"operation": "close"}))["result"]["closed"]
        await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
