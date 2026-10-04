"""Sandbox contracts exercise the shared engine with application permissions."""

import asyncio
import base64
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import zipfile
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.modules.tools.coding.adapter import SCHEMAS
from agent.modules.tools.coding.models import CodingError, InvocationContext, PermissionRule, ToolDefinition
from agent.modules.tools.coding.remote import BOOTSTRAP, SandboxClient, build_runtime_bundle
from agent.modules.tools.coding.remote_worker import SandboxWorker, permission_identity
from agent.modules.tools.coding.service import CodingService
from agent.modules.workspaces import CommandResult, WorkspaceRef


@pytest.fixture(params=["daytona", "modal"])
def remote_coding(tmp_path, isolated_container, monkeypatch, request):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worker = SandboxWorker(tmp_path / "remote-output", str(workspace))
    service = CodingService(tmp_path / "application-output")
    isolated_container._coding_service = service
    context = InvocationContext("default", str(workspace), "thread", backend=request.param, locator="sandbox")

    class Client:
        endpoint = {"runtime_dir": "runtime"}
        backend = SimpleNamespace(ref=WorkspaceRef(backend=request.param, locator="sandbox"), root=str(workspace))

        async def ensure_started(self):
            pass

        async def call(self, payload):
            return await worker.dispatch(payload)

    client = Client()
    service.remote.clients[(context.backend, context.locator, context.workspace)] = client
    return service, context, workspace, worker


async def invoke(service, context, name, **args):
    async def execute(parsed, ctx, runtime):
        return await runtime.execute(name, parsed.model_dump(), ctx)
    return await service.invoke(ToolDefinition(name, name, SCHEMAS[name], execute), args, context)


def python_command(source):
    payload = base64.b64encode(source.encode()).decode()
    argument = f"import base64;exec(base64.b64decode('{payload}'))"
    if os.name == "nt":
        return f'& "{sys.executable}" -c "{argument}"'
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(argument)}"


@pytest.mark.asyncio
async def test_remote_versioned_edits_write_search_and_replay(remote_coding):
    service, context, workspace, _ = remote_coding
    source = workspace / "source.py"
    source.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    page = await invoke(service, context, "read", file_path="source.py", limit=1)
    assert page.data["next_offset"] == 2
    assert page.data["version"] == hashlib.sha256(source.read_bytes()).hexdigest()
    edited = await invoke(service, context, "edit", file_path="source.py", old_string="one", new_string="first",
                          expected_version=page.data["version"])
    assert edited.status == "success"
    assert source.read_bytes() == b"\xef\xbb\xbffirst\r\ntwo\r\n"
    stale = await invoke(service, context, "write", file_path="source.py", content="lost",
                         expected_version=page.data["version"])
    assert stale.error.code == "stale_content"
    call = replace(context, tool_call_id="write", message_id="message")
    args = {"file_path": "new.py", "content": "first\n"}
    applied = await invoke(service, call, "write", **args)
    assert applied.status == "success"
    (workspace / "new.py").write_text("outside change")
    replay = await invoke(service, call, "write", **args)
    assert replay.model_dump() == applied.model_dump()
    assert (workspace / "new.py").read_text() == "outside change"
    grep = await invoke(service, context, "grep", pattern="first", fixed_strings=True)
    assert "source.py:1" in grep.content
    glob = await invoke(service, context, "glob", pattern="**/*.py")
    assert "source.py" in glob.content and "new.py" in glob.content
    listing = await invoke(service, context, "list_dir", limit=1)
    assert listing.data["next_offset"] == 2


@pytest.mark.asyncio
async def test_remote_permissions_are_checked_before_mutation(remote_coding):
    service, context, workspace, _ = remote_coding
    denied = replace(context, permission_rules=(PermissionRule(action="edit", resource="*", effect="deny"),))
    result = await invoke(service, denied, "write", file_path="blocked.txt", content="secret")
    assert result.error.code == "permission_denied"
    assert not (workspace / "blocked.txt").exists()
    asked = replace(context, permission_rules=(PermissionRule(action="edit", resource="*", effect="ask"),))
    result = await invoke(service, asked, "write", file_path="pending.txt", content="secret")
    assert result.error.code == "approval_unavailable"
    assert not (workspace / "pending.txt").exists()
    escape = await invoke(service, context, "read", file_path="../outside.txt")
    assert escape.error.code == "permission_denied"


@pytest.mark.asyncio
async def test_remote_skill_mutations_invalidate_discovery_only_after_commit(remote_coding):
    from agent.shared.infrastructure.revisions import SKILLS_REVISION, get_revision
    service, context, _, _ = remote_coding
    before = get_revision(SKILLS_REVISION)
    changed = await invoke(service, context, "write", file_path=".agent/skills/demo/SKILL.md", content="skill")
    assert changed.status == "success" and get_revision(SKILLS_REVISION) > before
    committed = get_revision(SKILLS_REVISION)
    rejected = await invoke(service, context, "write", file_path=".agent/skills/demo/SKILL.md",
                            content="stale", expected_version="stale")
    assert rejected.error.code == "stale_content"
    assert get_revision(SKILLS_REVISION) == committed


@pytest.mark.asyncio
async def test_remote_process_stdin_cursor_output_ownership_and_cleanup(remote_coding):
    service, context, _, worker = remote_coding
    result = await invoke(service, context, "bash",
                          command=python_command("import sys,time; print('ready',flush=True); print(sys.stdin.readline(),flush=True); time.sleep(30)"),
                          yield_time_ms=0)
    assert result.status == "running"
    process_id = result.data["process_id"]
    sent = await invoke(service, context, "write_process_input", process_id=process_id, text="hello\n", yield_time_ms=1000)
    for _ in range(10):
        if "hello" in sent.content:
            break
        sent = await invoke(service, context, "read_process_output", process_id=process_id, yield_time_ms=1000)
    assert "hello" in sent.content
    read = await invoke(service, context, "read_process_output", process_id=process_id)
    again = await invoke(service, context, "read_process_output", process_id=process_id)
    assert read.content == again.content and read.data["cursor"] == again.data["cursor"]
    output = await invoke(service, context, "read", file_path=result.output_paths[0])
    assert "hello" in output.content
    foreign = await invoke(service, replace(context, thread_id="another"), "read_process_output", process_id=process_id)
    assert foreign.error.code == "not_found"
    await service.remote.stop_thread(context.thread_id)
    assert not worker.processes.jobs


@pytest.mark.asyncio
async def test_worker_rejects_resources_that_changed_after_authorization(remote_coding):
    service, context, workspace, worker = remote_coding
    payload = service.remote.request("prepare", "write", {"file_path": "target.txt", "content": "new"}, context)
    await worker.dispatch(payload)
    payload["operation"] = "execute"
    payload["authorized"] = []
    with pytest.raises(CodingError, match="changed after"):
        await worker.dispatch(payload)
    assert not (workspace / "target.txt").exists()


def test_bundle_contains_shared_engine_and_minimal_package_initializers():
    with zipfile.ZipFile(io.BytesIO(build_runtime_bundle())) as archive:
        assert "agent/modules/tools/coding/engine.py" in archive.namelist()
        assert "agent/bootstrap/container.py" not in archive.namelist()
        assert archive.read("agent/__init__.py") == b""


def test_retired_allow_list_does_not_expand_to_all_tools():
    from agent.modules.agents.models import AgentConfig
    from agent.modules.tools import ToolPolicy
    config = AgentConfig(name="restricted", graph_type="react_agent", provider="default",
                         tools=["bash_list_sessions"])
    policy = ToolPolicy.from_agent_config(config)
    assert policy.allowed_tool_names == frozenset({"bash_list_sessions"})


def test_agent_allow_lists_normalize_and_deduplicate_old_command_names():
    from agent.modules.agents.models import AgentConfig
    config = AgentConfig(name="coder", graph_type="react_agent", provider="default",
                         tools=["run_bash", "bash", "bash", "bash_read_output", "bash_close"])
    assert config.tools == ["bash", "read_process_output", "stop_process"]


def test_remote_workspace_owners_do_not_collide_at_field_boundaries():
    first = InvocationContext("default", "/root:/child", "thread", backend="daytona", locator="sandbox")
    second = InvocationContext("default", "/child", "thread", backend="daytona", locator="sandbox:/root")
    assert first.owner != second.owner


def test_sandbox_glob_fallback_matches_standard_translation(monkeypatch):
    import glob
    import re
    from agent.shared.infrastructure.glob_utils import translate_path_glob
    patterns = ["**/*.py", "*.py", "src/**/test[!0-9]?.py", "**/**/file.*", "a*b*c", "**/.hidden", "**/[[]file].py",
                "**/[].].py", "**/[!].].py", "**/[[]?.py"]
    candidates = ["main.py", "src/main.py", "src/tests/testab.py", "src/tests/test1b.py", "src/.hidden",
                  "file.py", "a/b/c", "a1b2c", "src/[file].py", ".hidden", "src/deep/main.py",
                  "].py", "..py", "[a.py", "[/.py"]
    expected = {pattern: [bool(re.match(translate_path_glob(pattern), path)) for path in candidates]
                for pattern in patterns}
    monkeypatch.delattr(glob, "translate")
    for pattern in patterns:
        assert [bool(re.match(translate_path_glob(pattern), path)) for path in candidates] == expected[pattern]


@pytest.mark.asyncio
async def test_remote_relative_root_hint_uses_backend_canonical_root(remote_coding):
    service, context, workspace, _ = remote_coding
    original = service.remote.clients[(context.backend, context.locator, context.workspace)]
    hinted = replace(context, workspace="workspace")
    service.remote.clients[(hinted.backend, hinted.locator, hinted.workspace)] = original
    result = await invoke(service, hinted, "write", file_path="canonical.txt", content="correct")
    assert result.status == "success"
    assert (workspace / "canonical.txt").read_text() == "correct"


@pytest.mark.asyncio
async def test_remote_structured_tool_adapter_preserves_artifacts(remote_coding):
    from agent.modules.tools.coding.adapter import make_coding_tool
    service, context, workspace, _ = remote_coding
    runtime = SimpleNamespace(context={"workspace": WorkspaceRef(backend=context.backend, locator=context.locator,
                                                                  metadata={"root": str(workspace)})},
                              config={"configurable": {"thread_id": context.thread_id}}, tool_call_id="remote-call")
    message = await make_coding_tool("write").coroutine(runtime=runtime, file_path="artifact.txt", content="content")
    assert message.status == "success"
    assert "version=" in message.content
    assert "+content" in message.artifact["display_content"]
    assert (workspace / "artifact.txt").read_text() == "content"


@pytest.mark.asyncio
async def test_remote_job_ownership_includes_sandbox_locator(remote_coding):
    service, context, _, worker = remote_coding
    started = await invoke(service, context, "bash", command=python_command("print('owned')"), yield_time_ms=1000)
    foreign = replace(context, locator="another-sandbox")
    payload = service.remote.request("prepare", "read_process_output", {"process_id": started.data["process_id"]}, foreign)
    with pytest.raises(CodingError, match="workspace/thread"):
        await worker.dispatch(payload)
    await service.remote.stop_thread(context.thread_id)


@pytest.mark.skipif(os.name == "nt", reason="Sandbox daemon requires a POSIX host.")
def test_bundle_imports_without_application_dependencies(tmp_path):
    bundle = tmp_path / "runtime.zip"
    bundle.write_bytes(build_runtime_bundle())
    code = ("import sys; sys.path.insert(0,sys.argv[1]); "
            "from agent.modules.tools.coding.remote_worker import SandboxWorker; "
            "assert 'pydantic' not in sys.modules; assert 'langchain_core' not in sys.modules")
    subprocess.run([sys.executable, "-I", "-c", code, str(bundle)], cwd=tmp_path, check=True, timeout=15)


@pytest.mark.asyncio
async def test_cancelled_call_cannot_start_after_its_cancel_request(remote_coding):
    service, context, workspace, worker = remote_coding
    await worker.dispatch({"operation": "cancel_call", "request_id": "cancelled"})
    request = service.remote.request("execute", "write", {"file_path": "late.txt", "content": "late"}, context)
    request["request_id"] = "cancelled"
    with pytest.raises(CodingError, match="cancelled"):
        await worker.dispatch(request)
    assert not (workspace / "late.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="Sandbox transport uses a Linux daemon.")
@pytest.mark.asyncio
async def test_real_bundle_daemon_and_file_rpc_transport(tmp_path):
    """Run the shipped zip and RPC protocol without cloud credentials."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    class Backend:
        root = str(workspace)

        async def upload_coding_file(self, content, path):
            from pathlib import Path
            Path(path).write_bytes(content)

        async def download_coding_file(self, path):
            from pathlib import Path
            return Path(path).read_bytes()

        async def execute_coding_command(self, command, *, timeout=30):
            from pathlib import Path
            environment = {**os.environ, "HOME": str(home),
                           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
            process = await asyncio.create_subprocess_exec("/bin/sh", "-c", command, env=environment,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
            return CommandResult((stdout + stderr).decode(), process.returncode)

    import uuid
    client = SandboxClient(Backend(), build_runtime_bundle(), uuid.uuid4().hex, uuid.uuid4().hex[:12])
    await client.ensure_started()
    context = InvocationContext("default", str(workspace), "thread", backend="modal", locator="test-sandbox")

    async def call(name, **values):
        values = SCHEMAS[name].model_validate(values).model_dump()
        request = {"operation": "prepare", "name": name, "values": values,
                   "context": {"agent_name": context.agent_name, "workspace": context.workspace,
                               "thread_id": context.thread_id, "backend": context.backend, "locator": context.locator}}
        prepared = await client.call(request)
        request["operation"] = "execute"
        request["authorized"] = [permission_identity(item["action"], item["resource"], item["metadata"])
                                 for item in prepared["permissions"]]
        return await client.call(request)

    try:
        written = await call("write", file_path="source.txt", content="hello\n")
        assert written["status"] == "success"
        assert (workspace / "source.txt").read_text() == "hello\n"
        image = b"\x89PNG\r\n\x1a\n" + b"image payload" * 50000
        (workspace / "image.png").write_bytes(image)
        viewed = await call("read", file_path="image.png")
        assert base64.b64decode(viewed["content"][1]["base64"]) == image
        job = await call("bash", command=python_command("import sys; print(sys.stdin.readline(),flush=True)"), yield_time_ms=0)
        observed = await call("write_process_input", process_id=job["data"]["process_id"], text="through RPC\n", yield_time_ms=3000)
        assert "through RPC" in observed["content"]
        assert observed["data"]["exit_code"] == 0
    finally:
        await client.call({"operation": "close"})


@pytest.mark.parametrize("backend_name", ["daytona", "modal"])
@pytest.mark.asyncio
async def test_provider_transport_preserves_large_payloads(backend_name):
    from agent.modules.workspaces.daytona_backend import DaytonaWorkspaceBackend
    from agent.modules.workspaces.modal_backend import ModalWorkspaceBackend
    backend_type = DaytonaWorkspaceBackend if backend_name == "daytona" else ModalWorkspaceBackend
    backend = object.__new__(backend_type)
    backend.root = "/workspace"
    backend.ref = WorkspaceRef(backend=backend_name, locator="sandbox")
    files = {}

    def upload(content, path):
        files[path] = content

    def execute(command):
        parts = shlex.split(command)
        if parts[2] == BOOTSTRAP:
            return json.dumps({"runtime_dir": "/tmp/runtime", "python": "/usr/bin/python3", "bundle": "/tmp/runtime/runtime.zip"})
        if "rpc(Path" in parts[2]:
            request = json.loads(files[parts[5]])
            files[parts[6]] = json.dumps({"result": request["values"]}, ensure_ascii=True).encode()
        return ""

    if backend_name == "daytona":
        backend.fs = SimpleNamespace(upload_file=upload, download_file=lambda path: files[path])
        backend.process = SimpleNamespace(exec=lambda command, **kwargs: SimpleNamespace(result=execute(command), exit_code=0))
    else:
        backend.fs = SimpleNamespace(write_bytes=upload, read_bytes=lambda path: files[path])

        async def exec_aio(*args, **kwargs):
            output = execute(args[2])
            return SimpleNamespace(stdout=output, stderr="", wait=lambda: 0)

        backend.sandbox = SimpleNamespace(exec=SimpleNamespace(aio=exec_aio))
    client = SandboxClient(backend, build_runtime_bundle(), "namespace", "generation")
    await client.ensure_started()
    values = {"content": "\u03bb" * 100000}
    response = await client.call({"operation": "execute", "values": values})
    assert response == values
