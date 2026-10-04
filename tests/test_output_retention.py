"""Contracts for lazy storage, exact output recovery, and conversation cleanup."""

import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

from agent.modules.tools.coding.adapter import SCHEMAS, make_coding_tool
from agent.modules.tools.coding.files import read_byte_page
from agent.modules.tools.coding.models import InvocationContext, ToolDefinition, ToolResult
from agent.modules.tools.coding.remote_worker import SandboxWorker
from agent.modules.tools.coding.service import CodingService
from agent.modules.tools.coding.storage import (
    MAX_MODEL_BYTES, RETENTION_SECONDS, OutputStore, conversation_key, digest, ensure_workspace_exclude,
)
from agent.modules.tools.runtime import output_retention, thread_storage
from agent.modules.workspaces import WorkspaceRef


@pytest.fixture(params=["local", "daytona", "modal"])
def output_environment(tmp_path, isolated_container, monkeypatch, request):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = CodingService(tmp_path / "application")
    isolated_container._coding_service = service
    context = InvocationContext("default", str(workspace), "conversation", backend=request.param, locator="sandbox")
    worker = None
    if request.param != "local":
        worker = SandboxWorker(tmp_path / "worker", str(workspace))

        class Client:
            endpoint = {"runtime_dir": "runtime"}
            backend = SimpleNamespace(root=str(workspace), ref=WorkspaceRef(backend=request.param, locator="sandbox"))

            async def ensure_started(self):
                pass

            async def call(self, payload):
                return await worker.dispatch(payload)

        service.remote.clients[(context.backend, context.locator, context.workspace)] = Client()
    monkeypatch.setattr(output_retention, "invocation_context", lambda runtime: context)
    return service, context, workspace, worker


async def invoke(service, context, name, **args):
    async def execute(params, ctx, runtime):
        return await runtime.execute(name, params.model_dump(), ctx)
    return await service.invoke(ToolDefinition(name, name, SCHEMAS[name], execute), args, context)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["web_fetch", "mcp__server__fetch"])
async def test_generic_output_preserves_media_errors_and_exact_long_lines(output_environment, tool_name):
    service, context, workspace, _ = output_environment
    text = json.dumps({"content": "emoji: \U0001f600, text\r\n" * 8000}, ensure_ascii=False)
    media = {"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"}
    message = ToolMessage(content=[{"type": "text", "text": text}, media], status="error",
                          name=tool_name, tool_call_id="call", id="message", artifact={"custom": "preserved"})
    runtime = SimpleNamespace(context={}, config={})
    result = await output_retention.retain_tool_messages(Command(update={"messages": [message], "other": 1}), runtime)
    retained = result.update["messages"][0]
    path = retained.artifact["output_paths"][0]
    assert retained.status == "error" and retained.id == "message" and retained.tool_call_id == "call"
    assert retained.artifact["custom"] == "preserved" and result.update["other"] == 1
    assert media in retained.content
    assert len(retained.content[0]["text"].encode()) <= MAX_MODEL_BYTES
    assert (workspace / path).read_bytes() == text.encode("utf-8")
    assert not (workspace / ".k41-agent" / "memory").exists()
    assert not (workspace / ".k41-agent" / "uploads").exists()
    cursor, recovered = 0, []
    while cursor is not None:
        page = await invoke(service, context, "read", file_path=path, byte_offset=cursor)
        assert page.status == "success" and not page.output_paths
        recovered.append(page.content.rsplit("\n[version=", 1)[0])
        cursor = page.data["next_byte_offset"]
    assert "".join(recovered) == text
    repeated = await output_retention.retain_message(retained, runtime)
    assert repeated is retained
    foreign = await invoke(service, replace(context, thread_id="other"), "read", file_path=path)
    assert foreign.error.code == "not_found"
    await service.processes.close()


@pytest.mark.asyncio
async def test_retention_failure_keeps_media_and_returns_no_broken_path(output_environment, monkeypatch):
    service, _, workspace, worker = output_environment
    store = worker.storage if worker else service.storage
    monkeypatch.setattr(store, "create_output", lambda context: (_ for _ in ()).throw(OSError("disk unavailable")))
    media = {"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"}
    message = ToolMessage(content=[{"type": "text", "text": "x" * 100000}, media], name="mcp_fetch", tool_call_id="call")
    retained = await output_retention.retain_message(message, SimpleNamespace(context={}, config={}))
    metadata = retained.additional_kwargs["output_retention"]
    assert not metadata["output_paths"] and metadata["capture_truncated"]
    assert "failed to retain" in retained.content[0]["text"] and media in retained.content
    assert not list(workspace.rglob("*.txt"))
    await service.processes.close()


@pytest.mark.asyncio
async def test_legacy_history_migration_is_repeatable(output_environment):
    service, context, workspace, worker = output_environment
    store = worker.storage if worker else service.storage
    reference = "a" * 32
    legacy = store.owner_dir(context) / f"{reference}.output"
    legacy.write_bytes(b"legacy log\n")
    message = ToolMessage(content=f"[output truncated; read_tool_output output_ref={reference}]",
                          name="bash", tool_call_id="call", artifact={"output_refs": [reference]})
    runtime = SimpleNamespace(context={}, config={})
    first = (await output_retention.migrate_history_outputs([message], runtime))[0]
    second = (await output_retention.migrate_history_outputs([message], runtime))[0]
    assert first.artifact["output_paths"] == second.artifact["output_paths"]
    assert (workspace / first.artifact["output_paths"][0]).read_bytes() == legacy.read_bytes()
    assert "read_tool_output" not in first.content and "read_tool_output" in message.content
    await service.processes.close()


def test_retention_quota_and_cleanup_protect_active_output_and_unmanaged_files(tmp_path, monkeypatch):
    store = OutputStore(tmp_path / "records")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = InvocationContext("default", str(workspace), "thread")
    monkeypatch.setattr("agent.modules.tools.coding.storage.MAX_STORED_BYTES", 4096)
    result = store.bound(ToolResult(content="\U0001f600" * 20000), context)
    retained = workspace / result.output_paths[0]
    assert retained.stat().st_size <= 4096 and result.capture_truncated
    retained.read_text(encoding="utf-8")
    old = time.time() - RETENTION_SECONDS - 60
    os.utime(retained, (old, old))
    unknown = retained.parent / "user-notes.txt"
    unknown.write_text("keep", encoding="utf-8")
    os.utime(unknown, (old, old))
    store.active_paths.add(retained)
    store.cleanup()
    assert retained.exists()
    store.active_paths.clear()
    store.cleanup()
    assert not retained.exists() and unknown.exists()


def test_lazy_storage_sync_and_scratchpad_reset_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "mirrors")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = thread_storage.ensure_physical_workspace_storage(workspace)
    assert not target.exists()
    root = thread_storage.ensure_workspace_storage_root("workspace")
    assert not list(root.iterdir())
    context = InvocationContext("default", str(workspace), "conversation:sub:worker:1")
    store = OutputStore(tmp_path / "records")
    store.owner_dir(context)
    note = target / "scratchpad" / conversation_key(context.thread_id) / "worker.md"
    other = target / "scratchpad" / conversation_key("other") / "note.md"
    unknown = target / "project" / "source.py"
    legacy = target / "assets" / "old.txt"
    for path in (note, other, unknown, legacy):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep", encoding="utf-8")
    thread_storage.sync_back_workspace_storage(workspace, "workspace")
    assert not (root / "project").exists() and not (root / "assets").exists()
    assert (root / note.relative_to(target)).exists()
    reloaded = OutputStore(tmp_path / "records")
    reloaded.clear_scratchpads("conversation")
    thread_storage.clear_persistent_scratchpads("conversation")
    assert not note.exists() and not (root / note.relative_to(target)).exists()
    assert other.exists() and legacy.exists() and unknown.exists()


def test_git_worktree_internal_storage_is_excluded(tmp_path):
    repo, worktree = tmp_path / "repo", tmp_path / "worktree"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "commit", "-q", "--allow-empty", "-m", "Initial"], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "test", str(worktree)], check=True)
    ensure_workspace_exclude(worktree)
    note = worktree / ".k41-agent" / "scratchpad" / "note.txt"
    note.parent.mkdir(parents=True)
    note.write_text("internal", encoding="utf-8")
    result = subprocess.run(["git", "-C", str(worktree), "check-ignore", ".k41-agent/scratchpad/note.txt"], capture_output=True)
    assert result.returncode == 0


def test_byte_cursor_rejects_partial_unicode_character(tmp_path):
    path = tmp_path / "unicode.txt"
    path.write_bytes("\U0001f600".encode())
    with pytest.raises(Exception, match="UTF-8 boundary"):
        read_byte_page(path, 1)


def test_retired_tool_does_not_expand_allowlist(isolated_container):
    from agent.modules.tools.resolver import ToolResolver
    resolver = ToolResolver(include_mcp=False)
    tools = resolver.resolve_for_agent("default", override_tool_names=["read_tool_output"])
    assert not [tool for tool in tools if tool.name in SCHEMAS]
    assert "read_tool_output" not in SCHEMAS


@pytest.mark.asyncio
async def test_automatic_output_reader_cannot_read_project_source(output_environment, monkeypatch):
    service, context, workspace, _ = output_environment
    (workspace / "source.py").write_text("private source", encoding="utf-8")
    import agent.modules.tools.coding.adapter as adapter
    monkeypatch.setattr(adapter, "invocation_context", lambda runtime: context)
    tool = make_coding_tool("read", retained_only=True)
    result = await tool.coroutine(runtime=SimpleNamespace(tool_call_id=None), file_path="source.py")
    assert "only read retained" in result
    await service.processes.close()


@pytest.mark.asyncio
async def test_actual_tool_node_retains_output_before_checkpoint(output_environment, monkeypatch):
    service, context, _, _ = output_environment
    from agent.modules.workflows.nodes.tool import tool_node
    from agent.modules.tools.resolver import ToolResolver
    from agent.modules.workflows.run_config import WorkflowContext
    text = "report line\n" * 8000

    @tool
    async def fetch_report() -> str:
        """Fetch a long report."""
        return text

    async def resolve(self, agent_name, **kwargs):
        return [fetch_report]

    monkeypatch.setattr(ToolResolver, "aresolve_for_agent", resolve)
    graph = StateGraph(MessagesState, context_schema=WorkflowContext)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": context.thread_id}}
    workspace = WorkspaceRef(backend=context.backend, locator=context.workspace if context.backend == "local" else context.locator,
                             metadata={"root": context.workspace})
    result = await compiled.ainvoke({"messages": [AIMessage(content="", tool_calls=[
        {"name": "fetch_report", "args": {}, "id": "fetch"}])]}, config,
        context=WorkflowContext(working_dir=context.workspace, workspace=workspace))
    retained = result["messages"][-1]
    assert len(retained.content.encode()) <= MAX_MODEL_BYTES
    assert retained.additional_kwargs["output_retention"]["output_paths"]
    state = await compiled.aget_state(config)
    assert state.values["messages"][-1].content == retained.content
    assert text not in retained.content
    await service.processes.close()


@pytest.mark.asyncio
async def test_byte_pages_with_many_lines_do_not_offload_again(output_environment):
    service, context, workspace, _ = output_environment
    text = "line\r\n" * 8000
    path = workspace / "lines.txt"
    path.write_bytes(text.encode())
    cursor, recovered = 0, []
    while cursor is not None:
        result = await invoke(service, context, "read", file_path="lines.txt", byte_offset=cursor)
        assert not result.output_paths and len(result.content.splitlines()) <= 2000
        recovered.append(result.content.rsplit("\n[version=", 1)[0])
        cursor = result.data["next_byte_offset"]
    assert "".join(recovered) == text
    await service.processes.close()


@pytest.mark.asyncio
async def test_sandbox_hydration_uses_a_single_storage_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "mirrors")
    source = thread_storage.workspace_storage_root("workspace") / "uploads" / "nested file.txt"
    source.parent.mkdir(parents=True)
    source.write_text("attachment", encoding="utf-8")
    written = {}
    commands = []
    class Backend:
        root = "/workspace"
        async def execute(self, command, **kwargs):
            commands.append(command)
        async def write_text(self, path, content, **kwargs):
            written[path] = content
    monkeypatch.setattr("agent.modules.workspaces.get_workspace_file_io", lambda *args, **kwargs: backend())
    async def backend():
        return Backend()
    workspace = WorkspaceRef(backend="modal", locator="sandbox")
    await thread_storage.ensure_sandbox_workspace_storage(workspace)
    assert not any("mkdir -p .k41-agent" in command for command in commands)
    await thread_storage.hydrate_workspace_storage_to_sandbox(workspace, "workspace")
    assert written == {".k41-agent/uploads/nested file.txt": "attachment"}


@pytest.mark.asyncio
async def test_legacy_invocation_replay_restores_paths_without_executing_again(output_environment):
    service, context, workspace, worker = output_environment
    context = replace(context, tool_call_id="legacy", message_id="message")
    store = worker.storage if worker else service.storage
    reference = "b" * 32
    legacy = store.owner_dir(context) / f"{reference}.output"
    legacy.write_bytes(b"old retained text")
    args = {"file_path": "missing.txt"}
    fingerprint = digest(json.dumps(["read", args], sort_keys=True, ensure_ascii=True))
    result = ToolResult(content=f"[read_tool_output output_ref={reference}]", output_refs=[reference])
    service.storage.complete(context, fingerprint, result)
    restored = await invoke(service, context, "read", **args, byte_offset=None)
    assert restored.status == "success" and restored.output_paths
    assert (workspace / restored.output_paths[0]).read_bytes() == b"old retained text"
    assert "read_tool_output" not in restored.content
    again = await invoke(service, context, "read", **args)
    assert again.output_paths == restored.output_paths
    assert not (workspace / "missing.txt").exists()
    await service.processes.close()
