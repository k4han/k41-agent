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
from agent.modules.tools.coding.models import CodingError, CodingInvocation, InvocationContext, PermissionRule, ToolDefinition
from agent.modules.tools.coding.remote import BOOTSTRAP, SandboxClient, build_runtime_bundle, coding_authorization_scope
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
async def test_project_credentials_are_supplied_after_remote_authorization(remote_coding, monkeypatch):
    service, context, workspace, worker = remote_coding
    resolved = []
    client = await service.remote.client(context)
    call = client.call
    requests = []

    async def capture(request):
        requests.append(request)
        return await call(request)

    async def credentials(current):
        assert requests and requests[-1]["operation"] == "prepare"
        resolved.append(current)
        return {"TOKEN": "remote-host-private-token"}

    monkeypatch.setattr(client, "call", capture)
    monkeypatch.setattr(service, "resolve_process_secrets", credentials)
    result = await invoke(service, context, "bash", command=python_command(
        "import os; assert os.environ['TOKEN']; print(os.environ['TOKEN'])"), yield_time_ms=0)
    assert result.error is None
    job = worker.processes.jobs[result.data["process_id"]]
    await job.finished.wait()
    assert job.process.returncode == 0 and job.retained_plain().strip() == "[REDACTED]"
    assert resolved == [context]
    execute_request = next(request for request in requests if request["operation"] == "execute")
    assert execute_request["execution_secrets"] == {"TOKEN": "remote-host-private-token"}
    assert "remote-host-private-token" not in json.dumps(execute_request["context"])
    assert "remote-host-private-token" not in json.dumps(execute_request["values"])
    await worker.processes.close()


@pytest.mark.asyncio
async def test_workspace_state_counts_running_and_retiring_jobs_without_mutating_them(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    worker = SandboxWorker(tmp_path / "outputs", str(root))
    running, finished = asyncio.Event(), asyncio.Event()
    finished.set()
    first = SimpleNamespace(workspace=str(root.resolve()), finished=running)
    second = SimpleNamespace(workspace=str(root.resolve()), finished=finished)
    retiring = SimpleNamespace(workspace=str(root.resolve()), finished=running)
    worker.processes.jobs = {"running": first, "finished": second}
    worker.processes.retiring_jobs = {"retiring": retiring}
    response = await worker.dispatch({"operation": "workspace_state", "workspace": str(root / ".." / "workspace")})
    assert response == {"running": 2}
    assert worker.processes.jobs["running"] is first
    assert not running.is_set()
    assert await worker.dispatch({"operation": "workspace_state", "workspace": str(tmp_path / "other")}) == {"running": 0}


@pytest.mark.asyncio
async def test_remote_shell_cleans_output_and_reports_nonzero_exit(remote_coding):
    service, context, _, worker = remote_coding
    try:
        result = await invoke(service, context, "bash", command=python_command(
            "import sys; sys.stdout.write('\\x1b[32mfailure\\x1b[0m\\n'); sys.exit(2)"
        ), yield_time_ms=30000)
        assert result.status == "error" and result.error.code == "nonzero_exit"
        assert result.content == "failure\n\n\nCommand exited with code 2."
        assert result.data["exit_code"] == 2
    finally:
        await service.processes.close()
        await worker.processes.close()


@pytest.mark.asyncio
async def test_remote_stop_process_succeeds_and_keeps_command_cancellation(remote_coding):
    service, context, _, worker = remote_coding
    try:
        started = await invoke(service, context, "bash", command=python_command("import time; time.sleep(30)"), yield_time_ms=0)
        process_id = started.data["process_id"]
        stopped = await invoke(service, context, "stop_process", process_id=process_id)
        assert stopped.status == "success" and stopped.error is None
        assert stopped.data["state"] == "cancelled"
        assert worker.processes.get(context, process_id).process.poll() is not None
        assert stopped.content.endswith("\n\nCommand was cancelled.")
        observed = await invoke(service, context, "read_process_output", process_id=process_id)
        assert observed.status == "error" and observed.error.code == "cancelled"
    finally:
        await service.processes.close()
        await worker.processes.close()


@pytest.mark.asyncio
async def test_remote_versioned_edits_write_search_and_replay(remote_coding):
    service, context, workspace, _ = remote_coding
    source = workspace / "source.py"
    source.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    page = await invoke(service, context, "read", path="source.py", limit=1)
    assert page.data["next_offset"] == 2
    assert page.data["version"] == hashlib.sha256(source.read_bytes()).hexdigest()
    edited = await invoke(service, context, "edit", path="source.py", old_string="one", new_string="first",
                          expected_version=page.data["version"])
    assert edited.status == "success"
    assert source.read_bytes() == b"\xef\xbb\xbffirst\r\ntwo\r\n"
    stale = await invoke(service, context, "write", path="source.py", content="lost",
                         expected_version=page.data["version"])
    assert stale.error.code == "stale_content"
    call = replace(context, tool_call_id="write", message_id="message")
    args = {"path": "new.py", "content": "first\n"}
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
    result = await invoke(service, denied, "write", path="blocked.txt", content="secret")
    assert result.error.code == "permission_denied"
    assert not (workspace / "blocked.txt").exists()
    asked = replace(context, permission_rules=(PermissionRule(action="edit", resource="*", effect="ask"),))
    result = await invoke(service, asked, "write", path="pending.txt", content="secret")
    assert result.error.code == "approval_unavailable"
    assert not (workspace / "pending.txt").exists()
    escape = await invoke(service, context, "read", path="../outside.txt")
    assert escape.error.code == "permission_denied"


@pytest.mark.asyncio
async def test_remote_parent_search_excludes_inactive_skill_snapshots(remote_coding):
    service, context, workspace, _ = remote_coding
    cache = workspace / ".k41-agent" / "s"
    active = cache / "sk-active"
    inactive = cache / "sk-inactive"
    active.mkdir(parents=True)
    inactive.mkdir()
    (active / "guide.md").write_text("VISIBLE active instructions", encoding="utf-8")
    (inactive / "private.md").write_text("SECRET inactive instructions", encoding="utf-8")
    context = replace(context, skill_roots=(str(active),))
    direct = await invoke(service, context, "read", path=".k41-agent/s/sk-inactive/private.md")
    assert direct.error.code == "not_found"
    for name, pattern in (("glob", "**/*.md"), ("grep", "VISIBLE|SECRET")):
        result = await invoke(service, context, name, path=".k41-agent", pattern=pattern)
        assert result.status == "success", result.content
        assert "guide.md" in result.content
        assert "private.md" not in result.content
        assert "SECRET" not in result.content


@pytest.mark.asyncio
async def test_migrated_remote_conversation_keeps_existing_outputs(remote_coding, isolated_container):
    from agent.shared.thread_ids import canonical_thread_id, thread_id_aliases_var
    from agent.modules.tools.coding.storage import output_relative_path

    service, context, _, worker = remote_coding
    old = "api_dashboard_saved"
    new = canonical_thread_id(old)
    legacy_context = replace(context, thread_id=old)
    reference, path = worker.storage.create_output(legacy_context)
    path.write_text("Saved output", encoding="utf-8")
    isolated_container._conversation_thread_aliases = {old: new}
    isolated_container._conversation_thread_storage_ids = {new: old}
    migrated = replace(context, thread_id=new)
    request = service.remote.request("prepare", "read", {"path": output_relative_path(legacy_context, reference)}, migrated)
    assert request["context"]["thread_id"] == new
    assert request["thread_aliases"] == {old: new}
    result = await invoke(service, migrated, "read", path=output_relative_path(legacy_context, reference))
    assert result.status == "success"
    assert "Saved output" in result.content
    assert thread_id_aliases_var.get() is None


@pytest.mark.asyncio
async def test_remote_skill_mutations_invalidate_discovery_only_after_commit(remote_coding):
    from agent.shared.infrastructure.revisions import SKILLS_REVISION, get_revision
    service, context, _, _ = remote_coding
    before = get_revision(SKILLS_REVISION)
    changed = await invoke(service, context, "write", path=".agent/skills/demo/SKILL.md", content="skill")
    assert changed.status == "success" and get_revision(SKILLS_REVISION) > before
    committed = get_revision(SKILLS_REVISION)
    rejected = await invoke(service, context, "write", path=".agent/skills/demo/SKILL.md",
                            content="stale", expected_version="stale")
    assert rejected.error.code == "stale_content"
    assert get_revision(SKILLS_REVISION) == committed


@pytest.mark.asyncio
async def test_remote_process_stdin_cursor_output_ownership_and_cleanup(remote_coding):
    service, context, workspace, worker = remote_coding
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
    assert not result.output_paths and not read.output_paths and not read.output_refs
    assert not (workspace / ".k41-agent" / "outputs").exists()
    foreign = await invoke(service, replace(context, thread_id="another"), "read_process_output", process_id=process_id)
    assert foreign.error.code == "not_found"
    await service.remote.stop_thread(context.thread_id)
    assert not worker.processes.jobs


@pytest.mark.asyncio
async def test_worker_rejects_resources_that_changed_after_authorization(remote_coding):
    service, context, workspace, worker = remote_coding
    payload = service.remote.request("prepare", "write", {"path": "target.txt", "content": "new"}, context)
    await worker.dispatch(payload)
    payload["operation"] = "execute"
    payload["authorized"] = []
    with pytest.raises(CodingError, match="changed after"):
        await worker.dispatch(payload)
    assert not (workspace / "target.txt").exists()


@pytest.mark.asyncio
async def test_invocation_prepares_once_and_does_not_cache_across_invocations(remote_coding, monkeypatch):
    service, context, workspace, _ = remote_coding
    (workspace / "file.txt").write_text("first")
    client = await service.remote.client(context)
    original = client.call
    operations = []

    async def call(request):
        operations.append(request["operation"])
        return await original(request)

    monkeypatch.setattr(client, "call", call)
    assert (await invoke(service, context, "read", path="file.txt")).status == "success"
    (workspace / "file.txt").write_text("second")
    result = await invoke(service, context, "read", path="file.txt")
    assert "second" in result.content
    assert operations == ["prepare", "execute", "prepare", "execute"]


@pytest.mark.asyncio
async def test_batch_prepares_once_for_parallel_calls_and_preserves_policy(remote_coding, monkeypatch):
    service, context, workspace, _ = remote_coding
    client = await service.remote.client(context)
    original = client.call
    operations = []

    async def call(request):
        operations.append(request["operation"])
        return await original(request)

    monkeypatch.setattr(client, "call", call)

    async def execute(params, ctx, runtime):
        return await runtime.execute("write", params.model_dump(), ctx)

    definition = ToolDefinition("write", "Write", SCHEMAS["write"], execute)
    invocations = [CodingInvocation(definition, {"path": name, "content": "written"},
                                    replace(context, tool_call_id=name)) for name in ("one.txt", "two.txt")]
    with coding_authorization_scope():
        assert await service.prepare_batch(invocations) is None
        assert not (workspace / "one.txt").exists()
        results = await asyncio.gather(*(service.invoke(item.definition, item.args, item.context) for item in invocations))
    assert all(result.status == "success" for result in results)
    assert operations == ["prepare_batch", "execute", "execute"]
    assert (workspace / "two.txt").read_text() == "written"

    restricted = replace(context, permission_rules=(PermissionRule(action="edit", resource="*blocked.txt", effect="deny"),))
    denied = [CodingInvocation(definition, {"path": name, "content": "denied"},
                               replace(restricted, tool_call_id=name)) for name in ("new.txt", "blocked.txt")]
    with coding_authorization_scope():
        failure = await service.prepare_batch(denied)
    assert failure.error.code == "permission_denied" and failure.data["batch_stopped_before_execution"]
    assert not (workspace / "new.txt").exists() and not (workspace / "blocked.txt").exists()


@pytest.mark.asyncio
async def test_cached_preparation_rechecks_changed_resources_in_worker(remote_coding):
    service, context, workspace, _ = remote_coding
    values = SCHEMAS["glob"].model_validate({"pattern": "*.txt"}).model_dump()
    with coding_authorization_scope():
        await service.remote.prepare("glob", values, context)
        (workspace / "new.txt").write_text("created after preparation")
        with pytest.raises(CodingError, match="changed after"):
            await service.remote.execute("glob", values, context)


@pytest.mark.asyncio
async def test_preparation_cache_cannot_authorize_a_changed_policy(remote_coding):
    service, context, workspace, _ = remote_coding
    values = SCHEMAS["write"].model_validate({"path": "policy.txt", "content": "blocked"}).model_dump()
    restricted = replace(context, permission_rules=(PermissionRule(action="edit", effect="deny"),))
    with coding_authorization_scope():
        await service.remote.prepare("write", values, context)
        with pytest.raises(CodingError, match="Policy denies"):
            await service.remote.execute("write", values, restricted)
    assert not (workspace / "policy.txt").exists()


@pytest.mark.asyncio
async def test_preparation_batches_cannot_hide_mutations(remote_coding):
    _, _, workspace, worker = remote_coding
    with pytest.raises(CodingError, match="cannot execute"):
        await worker.dispatch({"operation": "prepare_batch", "requests": [{"operation": "execute"}]})
    assert not list(workspace.iterdir())


def test_bundle_contains_shared_engine_and_minimal_package_initializers():
    with zipfile.ZipFile(io.BytesIO(build_runtime_bundle())) as archive:
        assert "agent/modules/tools/coding/engine.py" in archive.namelist()
        assert "agent/modules/tools/runtime/output_policy.py" in archive.namelist()
        assert "agent/modules/prompt_variables/environment.py" in archive.namelist()
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
    result = await invoke(service, hinted, "write", path="canonical.txt", content="correct")
    assert result.status == "success"
    assert (workspace / "canonical.txt").read_text() == "correct"


@pytest.mark.asyncio
async def test_remote_structured_tool_adapter_preserves_artifacts(remote_coding):
    from agent.modules.tools.coding.adapter import make_coding_tool
    service, context, workspace, _ = remote_coding
    runtime = SimpleNamespace(context={"workspace": WorkspaceRef(backend=context.backend, locator=context.locator,
                                                                  metadata={"root": str(workspace)})},
                              config={"configurable": {"thread_id": context.thread_id}}, tool_call_id="remote-call")
    message = await make_coding_tool("write").coroutine(runtime=runtime, path="artifact.txt", content="content")
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
    code = ("import asyncio, sys; sys.path.insert(0,sys.argv[1]); "
            "from agent.modules.tools.coding.remote_worker import SandboxWorker; "
            "from agent.modules.prompt_variables.environment import get_execution_environment; "
            "from agent.modules.tools.coding.processes import resolve_shell; "
            "worker = object.__new__(SandboxWorker); "
            "environment = asyncio.run(worker.dispatch({'operation': 'environment'})); "
            "assert environment == get_execution_environment(resolve_shell()); "
            "assert 'pydantic' not in sys.modules; assert 'langchain_core' not in sys.modules")
    subprocess.run([sys.executable, "-I", "-c", code, str(bundle)], cwd=tmp_path, check=True, timeout=15)


@pytest.mark.asyncio
async def test_cancelled_call_cannot_start_after_its_cancel_request(remote_coding):
    service, context, workspace, worker = remote_coding
    await worker.dispatch({"operation": "cancel_call", "request_id": "cancelled"})
    request = service.remote.request("execute", "write", {"path": "late.txt", "content": "late"}, context)
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
        written = await call("write", path="source.txt", content="hello\n")
        assert written["status"] == "success"
        assert (workspace / "source.txt").read_text() == "hello\n"
        image = b"\x89PNG\r\n\x1a\n" + b"image payload" * 50000
        (workspace / "image.png").write_bytes(image)
        viewed = await call("read", path="image.png")
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
        if "rpc_output(Path" in parts[2]:
            request = json.loads(files[parts[5]]) if parts[5] else json.loads(base64.b64decode(parts[7]))
            response = {"result": request["values"]}
            output = json.dumps({"response": response}, ensure_ascii=True)
            if len(output) > 32 * 1024:
                files[parts[6]] = json.dumps(response, ensure_ascii=True).encode()
                return json.dumps({"response_file": True})
            return output
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
