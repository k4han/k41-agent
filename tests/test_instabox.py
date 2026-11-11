"""Instabox integration tests against an in-memory fake of the REST API."""

from __future__ import annotations

import asyncio
import itertools
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio

from agent.modules.instabox import (
    InstaboxApiError,
    InstaboxClient,
    InstaboxConfirmationError,
    InstaboxConflictError,
    InstaboxService,
    InstaboxSettings,
    normalize_machine_state,
    redact,
    status_from_error,
)
from agent.modules.workspaces import WorkspaceRef

API_KEY = "ibk_" + "a" * 48


class FakeInstabox:
    """Minimal stateful stand-in for https://api.instabox.sh/v1."""

    def __init__(self) -> None:
        self.ids = itertools.count(1)
        self.machines: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.idempotent: dict[str, Any] = {}
        self.files: dict[tuple[str, str], bytes] = {}
        self.ports: dict[str, dict[int, dict[str, Any]]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.desktop: dict[str, dict[str, Any]] = {}
        self.leases: dict[tuple[str, str], int] = {}
        self.requests: list[httpx.Request] = []
        self.exec_calls: list[dict[str, Any]] = []
        self.exec_result: Any = None
        self.fail_next: list[Any] = []
        self.on_request = None

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), base_url="https://api.instabox.sh")

    def add_machine(self, state: str = "running", **fields: Any) -> str:
        machine_id = f"m_{next(self.ids):026d}"
        self.machines[machine_id] = {"id": machine_id, "name": fields.pop("name", machine_id), "state": state,
                                     "metadata": fields.pop("metadata", {}), **fields}
        return machine_id

    def _operation(self, kind: str, machine_id: str, effect=None) -> dict[str, Any]:
        op_id = f"op_{next(self.ids)}"
        operation = {"id": op_id, "type": kind, "machineId": machine_id, "state": "queued", "_effect": effect}
        self.operations[op_id] = operation
        return {k: v for k, v in operation.items() if not k.startswith("_")}

    @staticmethod
    def error(status: int, code: str, **extra: Any) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": code, "message": code.replace("_", " "), **extra}})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(request)
        if request.headers.get("X-API-Key") != API_KEY:
            return self.error(401, "invalid_key")
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        key = request.headers.get("Idempotency-Key")
        if key and key in self.idempotent:
            return httpx.Response(202, json=self.idempotent[key])
        response = self.route(request)
        if key and response.status_code < 300:
            self.idempotent[key] = response.json() if response.content else None
        return response

    def route(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        parts = path.strip("/").split("/")
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}
        if parts[:2] == ["v1", "machines"] and len(parts) == 2:
            if method == "GET":
                items = list(self.machines.values())
                limit = int(request.url.params.get("limit") or 100)
                start = int(request.url.params.get("cursor") or 0)
                page = items[start:start + limit]
                cursor = str(start + limit) if start + limit < len(items) else None
                return httpx.Response(200, json={"machines": page, "nextCursor": cursor})
            machine_id = f"m_{next(self.ids):026d}"

            def create():
                self.machines[machine_id] = {"id": machine_id, "name": body.get("name") or machine_id,
                                             "state": "running", "metadata": body.get("metadata") or {},
                                             "template": body.get("templateId"), "snapshot": body.get("snapshotId")}
            return httpx.Response(202, json=self._operation("create", machine_id, create))
        if parts[:2] == ["v1", "operations"]:
            operation = self.operations[parts[2]]
            if operation["state"] not in {"succeeded", "failed"}:
                if operation["_effect"]:
                    operation["_effect"]()
                operation["state"] = "succeeded"
            return httpx.Response(200, json={k: v for k, v in operation.items() if not k.startswith("_")})
        if parts[:2] == ["v1", "snapshots"]:
            if len(parts) == 2:
                return httpx.Response(200, json={"snapshots": list(self.snapshots.values())})
            snapshot = self.snapshots.get(parts[2])
            if snapshot is None:
                return self.error(404, "snapshot_not_found")
            if method == "DELETE":
                if request.headers.get("X-Confirm-Delete") != parts[2]:
                    return self.error(409, "delete_confirmation_required")
                del self.snapshots[parts[2]]
                return httpx.Response(204)
            return httpx.Response(200, json=snapshot)
        machine_id = parts[2]
        machine = self.machines.get(machine_id)
        if machine is None:
            return self.error(404, "machine_not_found")
        action = parts[3] if len(parts) > 3 else ""
        if not action:
            if method == "DELETE":
                if request.headers.get("X-Confirm-Delete") != machine_id:
                    return self.error(409, "delete_confirmation_required")
                return httpx.Response(202, json=self._operation("delete", machine_id, lambda: self.machines.pop(machine_id, None)))
            return httpx.Response(200, json=machine)
        if action in {"start", "resume", "stop", "suspend", "restore"}:
            target = {"start": "running", "resume": "running", "stop": "stopped", "suspend": "suspended", "restore": "running"}[action]
            return httpx.Response(202, json=self._operation(action, machine_id, lambda: machine.update(state=target)))
        if action == "fork":
            if machine["state"] == "suspended":
                return self.error(409, "fork_unavailable")
            new_id = f"m_{next(self.ids):026d}"
            return httpx.Response(202, json=self._operation("create", new_id, lambda: self.machines.__setitem__(
                new_id, {"id": new_id, "name": body.get("name") or new_id, "state": "running",
                         "metadata": {"instabox-fork-of": machine_id}})))
        if action == "exec":
            if machine["state"] != "running":
                return self.error(409, "machine_not_running")
            self.exec_calls.append(body)
            result = self.exec_result(body) if callable(self.exec_result) else self.exec_result
            return httpx.Response(200, json=result or {"stdout": "", "stderr": "", "exitCode": 0})
        if action == "files":
            path = request.url.params["path"]
            if len(parts) > 4 and parts[4] == "content":
                if (machine_id, path) not in self.files:
                    return self.error(404, "file_not_found")
                return httpx.Response(200, content=self.files[(machine_id, path)])
            if method == "PUT":
                self.files[(machine_id, path)] = request.content
                return httpx.Response(201, json={"path": path, "size": len(request.content)})
            if (machine_id, path) in self.files:
                return httpx.Response(200, json={"path": path, "type": "file", "size": len(self.files[(machine_id, path)])})
            prefix = path.rstrip("/") + "/"
            entries = [{"name": p[len(prefix):].split("/")[0], "type": "file" if "/" not in p[len(prefix):] else "dir"}
                       for (m, p) in self.files if m == machine_id and p.startswith(prefix)]
            if not entries:
                return self.error(404, "file_not_found")
            return httpx.Response(200, json={"path": path, "type": "dir", "entries": entries})
        if action == "ports":
            ports = self.ports.setdefault(machine_id, {})
            if method == "GET":
                return httpx.Response(200, json={"ports": list(ports.values())})
            if method == "DELETE":
                ports.pop(int(parts[4]), None)
                return httpx.Response(204)
            port = int(body["port"])
            existing = ports.get(port)
            entry = {"port": port, "access": body.get("access", "public"),
                     "url": f"https://{port}-{machine_id[2:]}.instabox.dev/"}
            ports[port] = entry
            result = dict(entry)
            if entry["access"] == "private" and (existing is None or existing.get("access") != "private" or body.get("rotateToken")):
                result["token"] = f"ibp_token{next(self.ids)}"
                result["tokenUrl"] = entry["url"] + "?_token=" + result["token"]
            return httpx.Response(201 if existing is None else 200, json=result)
        if action == "snapshots":
            snapshot_id = f"sn_{next(self.ids)}"
            self.snapshots[snapshot_id] = {"id": snapshot_id, "name": body["name"], "state": "pending"}
            return httpx.Response(202, json=self.snapshots[snapshot_id])
        if action == "desktop":
            state = self.desktop.setdefault(machine_id, {"installed": True, "running": False, "failed": False,
                                                        "installing": False, "agentPausable": True})
            if body.get("action") == "start":
                state["running"] = True
            if body.get("action") == "install":
                state["installed"] = True
            if body.get("action") == "takeover":
                state["controller"] = "human"
            if body.get("action") == "handback":
                state["controller"] = "agent"
            return httpx.Response(200, json=state)
        if action == "activity-leases":
            lease = (machine_id, parts[4])
            if method == "PUT":
                self.leases[lease] = self.leases.get(lease, 0) + 1
            else:
                self.leases.pop(lease, None)
            return httpx.Response(204)
        return self.error(404, "not_found")


class MemoryRepository:
    """Operation and port-token store with the repository interface."""

    def __init__(self) -> None:
        self.operations: dict[str, dict[str, Any]] = {}
        self.tokens: dict[tuple[str, int], tuple[str, str]] = {}
        self.ids = itertools.count(1)

    async def create_operation(self, *, idempotency_key, kind, target, machine_id=None, created_by=None):
        record = {"id": next(self.ids), "idempotency_key": idempotency_key, "kind": kind, "operation_id": None,
                  "machine_id": machine_id, "target": target, "status": "submitting", "phase": None,
                  "result": None, "error": None, "created_by": created_by}
        self.operations[idempotency_key] = record
        return dict(record)

    async def update_operation(self, idempotency_key, **fields):
        record = self.operations[idempotency_key]
        record.update(fields)
        return dict(record)

    async def get_operation(self, record_id):
        return next((dict(r) for r in self.operations.values() if r["id"] == record_id), None)

    async def get_operation_by_key(self, key):
        record = self.operations.get(key)
        return dict(record) if record else None

    async def list_operations(self, *, machine_id=None, active_only=False, limit=50):
        rows = [dict(r) for r in self.operations.values()
                if (not machine_id or r["machine_id"] == machine_id)
                and (not active_only or r["status"] in {"submitting", "queued", "running"})]
        return rows[:limit]

    async def save_port_token(self, machine_id, port, token, url):
        self.tokens[(machine_id, int(port))] = (token, url)

    async def port_token(self, machine_id, port):
        return self.tokens.get((machine_id, int(port)))

    async def delete_port_token(self, machine_id, port=None):
        for key in [k for k in self.tokens if k[0] == machine_id and (port is None or k[1] == int(port))]:
            self.tokens.pop(key)


async def _no_sleep(_seconds):
    return None


@pytest.fixture
def fake():
    return FakeInstabox()


@pytest_asyncio.fixture
async def service(fake):
    settings = InstaboxSettings(enabled=True, api_key=API_KEY)
    ibx = InstaboxService(SimpleNamespace(), repository=MemoryRepository(), http_client=fake.client(),
                          settings_loader=lambda: settings)
    yield ibx
    await ibx.close()


def client_for(fake, **kwargs) -> InstaboxClient:
    return InstaboxClient(api_key=API_KEY, client=fake.client(), sleep=_no_sleep, **kwargs)


# Client ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_client_sends_api_key_and_parses_error_codes(fake):
    machine_id = fake.add_machine()
    client = client_for(fake)
    assert (await client.get_machine(machine_id))["id"] == machine_id
    assert fake.requests[-1].headers["X-API-Key"] == API_KEY

    with pytest.raises(InstaboxApiError) as caught:
        await client.get_machine("m_missing")
    assert caught.value.code == "machine_not_found" and caught.value.is_not_found

    bad = InstaboxClient(api_key="ibk_wrong", client=fake.client(), sleep=_no_sleep)
    with pytest.raises(InstaboxApiError) as denied:
        await bad.list_machines()
    assert denied.value.is_auth_error


@pytest.mark.asyncio
async def test_reads_retry_but_commands_never_retry(fake):
    machine_id = fake.add_machine()
    client = client_for(fake)
    fake.fail_next = [fake.error(503, "host_unavailable", retryable=True)]
    assert (await client.get_machine(machine_id))["id"] == machine_id

    fake.fail_next = [httpx.ConnectError("reset")]
    with pytest.raises(httpx.ConnectError):
        await client.exec(machine_id, cmd="echo once")
    fake.fail_next = [fake.error(503, "host_unavailable", retryable=True)]
    with pytest.raises(InstaboxApiError):
        await client.exec(machine_id, cmd="echo once")
    exec_requests = [r for r in fake.requests if r.url.path.endswith("/exec")]
    assert len(exec_requests) == 2


@pytest.mark.asyncio
async def test_pagination_and_operation_wait(fake):
    for _ in range(5):
        fake.add_machine()
    client = client_for(fake)
    machines = [m async for m in client.iter_machines(page_size=2)]
    assert len(machines) == 5

    operation = await client.machine_action(machines[0]["id"], "stop", idempotency_key="k1")
    finished = await client.wait_operation(operation["id"], timeout=5)
    assert finished["state"] == "succeeded"
    assert fake.machines[machines[0]["id"]]["state"] == "stopped"
    assert "wait=" in str(fake.requests[-1].url)


@pytest.mark.asyncio
async def test_delete_requires_confirmation_header(fake):
    machine_id = fake.add_machine()
    client = client_for(fake)
    await client.delete_machine(machine_id, confirm=machine_id, idempotency_key="del-1")
    request = fake.requests[-1]
    assert request.headers["X-Confirm-Delete"] == machine_id
    assert request.headers["Idempotency-Key"] == "del-1"


def test_redact_hides_keys_and_tokens():
    text = redact(f"key {API_KEY} url https://3000-x.dev/?_token=ibp_secret123 proto instabox-access-token.abc")
    assert API_KEY not in text and "ibp_secret123" not in text and "abc" not in text.split("token.")[-1]
    assert redact({"token": "ibp_x", "nested": [API_KEY]}) == {"token": "[REDACTED]", "nested": ["[REDACTED]"]}


def test_state_normalization():
    assert normalize_machine_state("running") == "started"
    assert normalize_machine_state("suspended") == "paused"
    assert normalize_machine_state("stopped") == "stopped"
    assert normalize_machine_state("deleted") == "destroyed"
    assert normalize_machine_state("resuming") == "pending"
    assert status_from_error(InstaboxApiError("gone", status=404, code="machine_not_found")) == "destroyed"
    assert status_from_error(httpx.ConnectError("down")) == "unknown"
    assert status_from_error(InstaboxApiError("bad key", status=401, code="invalid_key")) == "unknown"


# Durable operations -------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_persists_idempotency_key_before_request(fake, service):
    seen = []

    def check(request):
        if request.method == "POST" and request.url.path == "/v1/machines":
            key = request.headers["Idempotency-Key"]
            seen.append(service.repository.operations[key]["status"])

    fake.on_request = check
    record = await service.create_machine(name="Dev Box", wait=True)
    assert seen == ["submitting"]
    assert record["status"] == "succeeded"
    assert record["machine_id"] in fake.machines
    created = json.loads([r for r in fake.requests if r.url.path == "/v1/machines" and r.method == "POST"][0].content)
    assert created["name"] == "dev-box" and created["templateId"] == "agent-browser"


@pytest.mark.asyncio
async def test_resume_resends_unanswered_create_without_duplicate(fake, service):
    original_handle = fake.handle
    state = {"drop": True}

    def lose_responses(request):
        response = original_handle(request)
        if state["drop"] and request.method == "POST" and request.url.path == "/v1/machines":
            raise httpx.ReadTimeout("response lost")
        return response

    service._http_client = httpx.AsyncClient(transport=httpx.MockTransport(lose_responses))
    service._client = None
    (await service.client())._sleep = _no_sleep
    with pytest.raises(httpx.ReadTimeout):
        await service.create_machine(name="box")
    state["drop"] = False
    pending = await service.repository.list_operations(active_only=True)
    assert len(pending) == 1 and pending[0]["status"] == "submitting"

    await service.resume_pending_operations()
    await asyncio.gather(*service._watchers.values())
    record = await service.repository.get_operation_by_key(pending[0]["idempotency_key"])
    assert record["status"] == "succeeded"
    assert len(fake.machines) == 1


@pytest.mark.asyncio
async def test_delete_and_snapshot_guards(fake, service):
    machine_id = fake.add_machine()
    with pytest.raises(InstaboxConfirmationError):
        await service.delete_machine(machine_id, confirm="wrong")
    record = await service.create_snapshot(machine_id, name="Base Image")
    assert record["status"] == "running"
    snapshot_id = record["operation_id"]
    with pytest.raises(InstaboxConflictError) as caught:
        await service.create_machine_from_snapshot(snapshot_id)
    assert caught.value.code == "snapshot_not_ready"
    fake.snapshots[snapshot_id]["state"] = "ready"
    created = await service.create_machine_from_snapshot(snapshot_id, wait=True)
    body = json.loads([r for r in fake.requests if r.url.path == "/v1/machines" and r.method == "POST"][-1].content)
    assert body.get("snapshotId") == snapshot_id and "templateId" not in body
    assert created["status"] == "succeeded"


@pytest.mark.asyncio
async def test_ensure_running_resumes_paused_machine(fake, service):
    machine_id = fake.add_machine(state="suspended")
    machine = await service.ensure_running(machine_id)
    assert machine["state"] == "running"
    assert any(r.url.path.endswith("/resume") for r in fake.requests)
    status_before = len(fake.requests)
    assert await service.machine_status(machine_id) == "started"
    assert all(r.method == "GET" for r in fake.requests[status_before:])


# Ports ------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_private_port_token_is_stored_and_never_returned_raw(fake, service):
    machine_id = fake.add_machine()
    result = await service.publish_port(machine_id, 3000)
    assert "token" not in result and "tokenUrl" not in result
    assert result["link"]["url"].startswith("https://3000-") and "_token=ibp_" in result["link"]["url"]
    assert json.loads(fake.requests[-1].content)["access"] == "private"

    link = await service.port_link(machine_id, 3000)
    assert "_token=ibp_" in link["url"]

    service.repository.tokens.clear()
    missing = await service.port_link(machine_id, 3000)
    assert missing["token_missing"] is True
    rotated = await service.rotate_port_token(machine_id, 3000)
    assert "_token=ibp_" in rotated["link"]["url"]

    public = await service.set_port_access(machine_id, 3000, "public")
    assert public["link"] == {"url": public["url"], "access": "public"}
    assert await service.repository.port_token(machine_id, 3000) is None


# Desktop ----------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_desktop_starts_once_and_runs_input_on_the_display(fake, service):
    from agent.modules.workspaces import ComputerStep
    from agent.modules.workspaces.instabox_backend import InstaboxComputer

    machine_id = fake.add_machine()
    computer = InstaboxComputer(service)
    await computer.act(machine_id, ComputerStep.parse({"action": "click", "x": 10, "y": 20, "button": "right"}))
    assert fake.desktop[machine_id]["running"] is True
    command = fake.exec_calls[-1]["cmd"]
    assert command.startswith('export DISPLAY="${DESKTOP_DISPLAY:-:1}"; ')
    assert command.endswith("xdotool mousemove 10 20 click 3")

    await computer.set_human_control(machine_id, True)
    assert fake.desktop[machine_id]["controller"] == "human"
    await computer.set_human_control(machine_id, False)
    assert fake.desktop[machine_id]["controller"] == "agent"


@pytest.mark.asyncio
async def test_desktop_typing_passes_text_through_environment(fake, service):
    from agent.modules.workspaces import ComputerError, ComputerStep
    from agent.modules.workspaces.instabox_backend import InstaboxComputer

    machine_id = fake.add_machine()
    await InstaboxComputer(service).act(machine_id, ComputerStep.parse({"action": "type", "text": "hello'; rm -rf /"}))
    call = fake.exec_calls[-1]
    assert call["envs"] == {"K41_DESKTOP_TEXT": "hello'; rm -rf /"}
    assert "rm -rf" not in call["cmd"]
    with pytest.raises(ComputerError):
        ComputerStep.parse({"action": "open_url", "url": "file:///etc/passwd"})


def test_computer_tools_must_run_one_per_machine_per_turn():
    from agent.modules.tools.policy import browser_batch_error

    calls = [{"name": "computer_action", "args": {}}, {"name": "computer_screenshot", "args": {}}]
    assert "one computer tool per machine" in browser_batch_error(calls)
    assert browser_batch_error([{"name": "computer_action", "args": {}},
                                {"name": "computer_screenshot", "args": {"target": "m_2"}}]) is None
    assert browser_batch_error([{"name": "computer_action"}, {"name": "instabox_port_list"}]) is None


def test_restore_always_requires_user_approval(tmp_path):
    from agent.modules.tools.coding.contracts import CodingError, InvocationContext
    from agent.modules.tools.coding.models import PermissionRule
    from agent.modules.tools.permissions import Permissions

    permissions = Permissions(SimpleNamespace(owner_dir=lambda context: tmp_path))
    allow_all = (PermissionRule(action="*", resource="*", effect="allow"),)
    context = InvocationContext(agent_name="a", workspace="/w", thread_id="t", permission_rules=allow_all)
    permissions.assert_allowed(context, "instabox_machine_delete", "m_1", allow_interrupt=False)
    with pytest.raises(CodingError):
        permissions.assert_allowed(context, "instabox_snapshot_restore", "m_1<-sn_1", allow_interrupt=False)
    default = InvocationContext(agent_name="a", workspace="/w", thread_id="t")
    with pytest.raises(CodingError):
        permissions.assert_allowed(default, "instabox_machine_delete", "m_1", allow_interrupt=False)


# Workspace backend --------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workspace_backend_exec_and_files(fake, service):
    from agent.modules.workspaces.instabox_backend import InstaboxWorkspaceBackend

    machine_id = fake.add_machine(state="suspended")
    ref = WorkspaceRef(backend="instabox", locator=machine_id, metadata={"root": "/data/home/workspace"})
    backend = InstaboxWorkspaceBackend(ref, service=service)
    fake.exec_result = {"stdout": "hi\n", "stderr": "warn", "exitCode": 0}

    result = await backend.execute("echo hi")
    assert result.exit_code == 0 and "hi" in result.output and "[stderr]: warn" in result.output
    assert fake.machines[machine_id]["state"] == "running"
    assert fake.exec_calls[-1]["cwd"] == "/data/home/workspace"

    payload = bytes(range(256))
    await backend.upload_coding_file(payload, "/data/home/workspace/bin.dat")
    assert await backend.download_coding_file("/data/home/workspace/bin.dat") == payload
    assert await backend.read_bytes("bin.dat") == payload
    tree = await backend.tree()
    assert [entry["name"] for entry in tree["entries"]] == ["bin.dat"]
    with pytest.raises(FileNotFoundError):
        await backend.read_bytes("missing.txt")


@pytest.mark.asyncio
async def test_workspace_backend_reports_lost_machine(fake, service):
    from agent.modules.workspaces import WorkspaceUnavailableError
    from agent.modules.workspaces.instabox_backend import InstaboxWorkspaceBackend

    ref = WorkspaceRef(backend="instabox", locator="m_gone", metadata={"root": "/data/home/workspace"})
    backend = InstaboxWorkspaceBackend(ref, service=service)
    with pytest.raises(WorkspaceUnavailableError):
        await backend.execute("true")


@pytest.mark.asyncio
async def test_passive_backend_does_not_wake_machine(fake, service):
    from agent.modules.workspaces import WorkspaceUnavailableError
    from agent.modules.workspaces.instabox_backend import InstaboxWorkspaceBackend

    machine_id = fake.add_machine(state="stopped")
    ref = WorkspaceRef(backend="instabox", locator=machine_id,
                       metadata={"root": "/data/home/workspace", "_require_running": True})
    backend = InstaboxWorkspaceBackend(ref, service=service)
    with pytest.raises(WorkspaceUnavailableError):
        await backend.ensure_active()
    assert fake.machines[machine_id]["state"] == "stopped"


@pytest.mark.asyncio
async def test_activity_lease_is_released(fake, service):
    machine_id = fake.add_machine()
    async with service.activity_lease(machine_id, "lease-1"):
        assert fake.leases[(machine_id, "lease-1")] == 1
    assert (machine_id, "lease-1") not in fake.leases


@pytest.mark.asyncio
async def test_repository_encrypts_port_tokens(isolated_container, tmp_path):
    from agent.modules.instabox.repository import InstaboxRepository
    from agent.shared.infrastructure.db import Base, load_orm_models
    from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine
    from agent.shared.infrastructure.secret_cipher import SecretCipher

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata, container=isolated_container)
    try:
        repository = InstaboxRepository(isolated_container, cipher=SecretCipher(tmp_path / "key"))
        await repository.save_port_token("m_1", 3000, "ibp_secret", "https://3000-1.dev/")
        assert await repository.port_token("m_1", 3000) == ("ibp_secret", "https://3000-1.dev/")
        from sqlalchemy import text
        async with repository.session() as session:
            stored = (await session.execute(text("SELECT token_encrypted FROM instabox_port_tokens"))).scalar_one()
        assert "ibp_secret" not in stored

        record = await repository.create_operation(idempotency_key="k", kind="create", target={"a": 1})
        assert record["status"] == "submitting"
        await repository.update_operation("k", status="succeeded", result={"ok": True})
        assert (await repository.list_operations(active_only=True)) == []
    finally:
        await close_async_engine(container=isolated_container)


# Dashboard routes ---------------------------------------------------------- #


def _dashboard(fake, isolated_container, *, enabled=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent.delivery.http.dashboard.routes.computers import viewer_router
    from agent.delivery.http.dashboard.routes.instabox import router
    from agent.modules.admin_auth import get_current_admin
    from agent.shared.config import ConfigService
    from agent.shared.config.default_source import DefaultConfigSource

    settings = InstaboxSettings(enabled=enabled, api_key=API_KEY if enabled else "")
    isolated_container._instabox_service = InstaboxService(
        isolated_container, repository=MemoryRepository(), http_client=fake.client(), settings_loader=lambda: settings
    )
    app = FastAPI()
    app.state.container = isolated_container
    isolated_container.config_service = ConfigService(sources=[DefaultConfigSource()])
    app.include_router(router)
    app.include_router(viewer_router)

    async def admin(_request=None):
        return "alice"

    app.dependency_overrides[get_current_admin] = admin
    return TestClient(app)


def test_dashboard_routes_guard_destructive_calls(fake, isolated_container):
    machine_id = fake.add_machine()
    client = _dashboard(fake, isolated_container)

    listed = client.get("/dashboard-api/instabox/machines")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["machines"]] == [machine_id]

    refused = client.request("DELETE", f"/dashboard-api/instabox/machines/{machine_id}", json={"confirm": "nope"})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "confirmation_required"
    assert machine_id in fake.machines

    missing = client.get("/dashboard-api/instabox/machines/m_missing")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "machine_not_found"

    low_port = client.post(f"/dashboard-api/instabox/machines/{machine_id}/ports", json={"port": 22})
    assert low_port.status_code == 422

    published = client.post(f"/dashboard-api/instabox/machines/{machine_id}/ports", json={"port": 8080})
    assert published.status_code == 200 and "token" not in published.json()
    assert API_KEY not in published.text


def test_dashboard_reports_disabled_instabox(fake, isolated_container):
    client = _dashboard(fake, isolated_container, enabled=False)
    response = client.get("/dashboard-api/instabox/machines")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "instabox_disabled"


def test_desktop_viewer_requires_origin_and_ticket(fake, isolated_container):
    from starlette.websockets import WebSocketDisconnect

    client = _dashboard(fake, isolated_container)
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/dashboard-api/computers/viewer", headers={"origin": "https://evil.test"}):
            pass
    assert rejected.value.code == 1008

    with client.websocket_connect("/dashboard-api/computers/viewer", headers={"origin": "http://testserver"}) as ws:
        ws.send_text(json.dumps({"ticket": "forged"}))
        assert ws.receive_json()["code"] == "ticket_invalid"
