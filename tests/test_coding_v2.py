"""Behavioral contracts for local coding execution, permissions and settlement."""

import asyncio
import base64
import hashlib
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from agent.modules.tools.coding.adapter import SCHEMAS, make_coding_tool
from agent.modules.tools.coding.models import CodingError, InvocationContext, PermissionRule, ToolDefinition, ToolResult
from agent.modules.tools.coding.service import CodingService
from agent.modules.tools.coding.storage import MAX_CAPTURE_BYTES, MAX_MODEL_BYTES, MAX_MODEL_LINES, bounded_text


@pytest.fixture
def coding(tmp_path, isolated_container):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = CodingService(tmp_path / "output")
    isolated_container._coding_service = service
    context = InvocationContext("default", str(workspace), "thread-1")
    return service, context, workspace


async def invoke(service, context, name, **args):
    async def execute(parsed, ctx, runtime):
        return await runtime.execute(name, parsed.model_dump(), ctx)
    definition = ToolDefinition(name, name, SCHEMAS[name], execute)
    return await service.invoke(definition, args, context)


def python_command(source):
    encoded = base64.b64encode(source.encode()).decode()
    argument = f"import base64;exec(base64.b64decode('{encoded}'))"
    if os.name == "nt":
        return f'& "{sys.executable}" -c "{argument}"'
    import shlex
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(argument)}"


@pytest.mark.asyncio
async def test_read_pages_version_and_stale_edit(coding):
    service, context, workspace = coding
    path = workspace / "source.txt"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    page = await invoke(service, context, "read_file", file_path="source.txt", limit=2)
    assert page.content.startswith("1: one\n2: two\n[version=")
    assert "next_offset=3" in page.content
    assert page.data["version"] == hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text("changed\n", encoding="utf-8")
    result = await invoke(service, context, "edit_file", file_path="source.txt", old_string="changed", new_string="next", expected_version=page.data["version"])
    assert result.error.code == "stale_content"
    assert path.read_text() == "changed\n"


@pytest.mark.asyncio
async def test_edit_preserves_bom_crlf_and_mode(coding):
    service, context, workspace = coding
    path = workspace / "source.txt"
    path.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    path.chmod(0o640)
    before_mode = path.stat().st_mode
    result = await invoke(service, context, "edit_file", file_path="source.txt", old_string="one\ntwo", new_string="first\nsecond")
    assert result.status == "success"
    assert path.read_bytes() == b"\xef\xbb\xbffirst\r\nsecond\r\n"
    assert path.stat().st_mode == before_mode
    assert result.data["additions"] == 2 and result.data["deletions"] == 2


@pytest.mark.asyncio
async def test_concurrent_versioned_edits_do_not_overwrite(coding):
    service, context, workspace = coding
    path = workspace / "source.txt"
    path.write_text("original", encoding="utf-8")
    version = hashlib.sha256(path.read_bytes()).hexdigest()
    results = await asyncio.gather(*(invoke(service, replace(context, tool_call_id=f"call-{index}"), "edit_file",
        file_path="source.txt", old_string="original", new_string=str(index), expected_version=version) for index in range(2)))
    assert sorted(result.status for result in results) == ["error", "success"]
    assert next(result.error.code for result in results if result.error) == "stale_content"


@pytest.mark.asyncio
async def test_write_append_and_no_op(coding):
    service, context, workspace = coding
    assert (await invoke(service, context, "write_file", file_path="new.txt", content="one\n")).status == "success"
    assert (await invoke(service, context, "write_file", file_path="new.txt", content="two\n", append=True)).status == "success"
    assert (workspace / "new.txt").read_text() == "one\ntwo\n"
    result = await invoke(service, context, "edit_file", file_path="new.txt", old_string="one", new_string="one")
    assert result.error.code == "invalid_input"


@pytest.mark.asyncio
async def test_large_line_and_many_lines_are_bounded(coding):
    service, context, workspace = coding
    path = workspace / "large.txt"
    path.write_text("x" * (3 * MAX_MODEL_BYTES) + "\n" + "next\n" * 3000, encoding="utf-8")
    first = await invoke(service, context, "read_file", file_path="large.txt", limit=1)
    assert len(first.content.encode()) <= MAX_MODEL_BYTES
    assert first.data["next_offset"] == 2
    second = await invoke(service, context, "read_file", file_path="large.txt", offset=2)
    assert second.content.startswith("2: next")
    assert len(second.content.splitlines()) <= MAX_MODEL_LINES


@pytest.mark.asyncio
async def test_search_regex_fixed_text_and_engine(coding, monkeypatch):
    service, context, workspace = coding
    (workspace / "a.py").write_text("literal [\nmatch\n", encoding="utf-8")
    invalid = await invoke(service, context, "grep", pattern="[")
    assert invalid.error.code == "invalid_input"
    literal = await invoke(service, context, "grep", pattern="[", fixed_strings=True)
    assert literal.data["matches"][0]["line"] == 1
    assert literal.data["engine"] in {"python", "ripgrep"}
    single = await invoke(service, context, "grep", pattern="match", path="a.py")
    assert single.status == "success" and single.data["engine"] == literal.data["engine"]
    monkeypatch.setattr("agent.modules.tools.coding.files.shutil.which", lambda name: None)
    fallback = await invoke(service, context, "grep", pattern="match")
    assert fallback.data["engine"] == "python"
    assert "a.py:2: match" in fallback.content


@pytest.mark.asyncio
async def test_external_symlink_is_not_searched(coding, tmp_path):
    service, context, workspace = coding
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (workspace / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink privilege is unavailable.")
    read = await invoke(service, context, "read_file", file_path="link.txt")
    assert read.error.code == "permission_denied"
    search = await invoke(service, context, "grep", pattern="secret")
    assert not search.data["matches"]


@pytest.mark.asyncio
async def test_permission_denial_and_unavailable_channel(coding):
    service, context, workspace = coding
    denied = replace(context, permission_rules=(PermissionRule(action="edit", effect="deny"),))
    result = await invoke(service, denied, "write_file", file_path="new.txt", content="new")
    assert result.error.code == "permission_denied"
    assert not (workspace / "new.txt").exists()
    asked = replace(context, permission_rules=(PermissionRule(action="edit", effect="ask"),))
    result = await invoke(service, asked, "write_file", file_path="new.txt", content="new")
    assert result.error.code == "approval_unavailable"


@pytest.mark.asyncio
async def test_patch_prevalidates_all_targets_and_reports_partial(coding, monkeypatch):
    service, context, workspace = coding
    (workspace / "a.txt").write_text("one\n", encoding="utf-8")
    invalid = "*** Begin Patch\n*** Update File: a.txt\n@@\n-one\n+two\n*** Update File: missing.txt\n@@\n-x\n+y\n*** End Patch"
    result = await invoke(service, context, "apply_patch", patch_text=invalid)
    assert result.status == "error" and (workspace / "a.txt").read_text() == "one\n"
    valid = "*** Begin Patch\n*** Update File: a.txt\n@@\n-one\n+two\n*** Add File: b.txt\n+new\n*** End Patch"
    original_commit = service.files.commit
    def fail_second(path, *args):
        if path.name == "b.txt":
            raise OSError("simulated write failure")
        return original_commit(path, *args)
    monkeypatch.setattr(service.files, "commit", fail_second)
    result = await invoke(service, replace(context, tool_call_id="patch-1"), "apply_patch", patch_text=valid)
    assert result.status == "partial_failure"
    assert len(result.data["applied"]) == 1 and len(result.data["pending"]) == 1
    assert "+two" not in result.content and "+two" in result.display_content
    assert "simulated write failure" in result.content and "b.txt" in result.content
    replay = await invoke(service, replace(context, tool_call_id="patch-1"), "apply_patch", patch_text=valid)
    assert replay == result


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "initial"), [
    ("write_file", {"file_path": "source.txt", "content": "submitted-content\n"}, None),
    ("write_file", {"file_path": "source.txt", "content": "submitted-content\n"}, "previous-content\n"),
    ("write_file", {"file_path": "source.txt", "content": "submitted-content\n", "append": True}, "previous-content\n"),
    ("edit_file", {"file_path": "source.txt", "old_string": "previous-content", "new_string": "submitted-content"}, "previous-content\n"),
    ("apply_patch", {"patch_text": "*** Begin Patch\n*** Add File: source.txt\n+submitted-content\n*** End Patch"}, None),
    ("apply_patch", {"patch_text": "*** Begin Patch\n*** Update File: source.txt\n@@\n-previous-content\n+submitted-content\n*** End Patch"}, "previous-content\n"),
    ("apply_patch", {"patch_text": "*** Begin Patch\n*** Delete File: source.txt\n*** End Patch"}, "previous-content\n"),
])
async def test_mutation_diffs_are_ui_only_in_tool_node_and_history(coding, monkeypatch, name, args, initial):
    from agent.modules.conversations.history import _serialize_thread_messages
    from agent.shared.infrastructure.parsing import extract_tool_display_content

    service, context, workspace = coding
    if initial is not None:
        (workspace / "source.txt").write_text(initial, encoding="utf-8")
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: None))
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([make_coding_tool(name)]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": context.thread_id}}
    result = await compiled.ainvoke({"messages": [AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": "mutation-call"},
    ])]}, config, context={"working_dir": str(workspace)})
    message = result["messages"][-1]
    assert message.status == "success"
    assert "submitted-content" not in message.content
    assert "previous-content" not in message.content
    assert "version=" in message.content
    expected_diff = "-previous-content" if "Delete File" in args.get("patch_text", "") else "+submitted-content"
    assert expected_diff in message.artifact["display_content"]
    assert expected_diff in extract_tool_display_content(message)
    assert _serialize_thread_messages([message])[0]["content"] == message.artifact["display_content"]
    assert convert_to_openai_messages([message])[0]["content"] == message.content
    saved = await compiled.aget_state(config)
    assert saved.values["messages"][-1].artifact == message.artifact


@pytest.mark.asyncio
async def test_large_mutation_diff_bounds_ui_without_inflating_model_content(coding):
    service, context, workspace = coding
    called = replace(context, tool_call_id="large-write")
    args = {"file_path": "large.txt", "content": "submitted-content\n" * 8000}
    result = await invoke(service, called, "write_file", **args)
    assert result.status == "success"
    assert "submitted-content" not in result.content and len(result.content.encode()) < 1024
    assert not result.output_truncated and result.display_truncated
    assert len(result.display_content.encode()) <= MAX_MODEL_BYTES
    assert len(result.display_content.splitlines()) <= MAX_MODEL_LINES
    assert result.data["diff_truncated"] and result.data["diff_output_refs"] == result.output_refs
    retained = await invoke(service, context, "read_tool_output", output_ref=result.output_refs[0])
    assert "+submitted-content" in retained.content
    assert (await invoke(service, called, "write_file", **args)) == result


@pytest.mark.asyncio
@pytest.mark.parametrize("as_tool_call", [False, True])
async def test_direct_tool_invocation_without_runtime_call_id_excludes_diff(coding, monkeypatch, as_tool_call):
    service, context, workspace = coding
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)
    args = {"file_path": "direct.txt", "content": "submitted-content\n", "runtime": SimpleNamespace(tool_call_id=None)}
    tool = make_coding_tool("write_file")
    payload = {"type": "tool_call", "name": tool.name, "id": "direct-call", "args": args} if as_tool_call else args
    result = await tool.ainvoke(payload)
    content = result.content if isinstance(result, ToolMessage) else result
    assert isinstance(content, str)
    assert "submitted-content" not in content
    assert "diff" not in content and "display_content" not in content
    assert "version=" in content
    assert (workspace / "direct.txt").read_text() == "submitted-content\n"


@pytest.mark.asyncio
async def test_legacy_journal_replay_removes_diff_without_repeating_mutation(coding):
    import json
    from agent.modules.tools.coding.storage import digest

    service, context, workspace = coding
    called = replace(context, tool_call_id="legacy-write")
    args = {"file_path": "old.txt", "content": "submitted-content\n", "append": True}
    first = await invoke(service, called, "write_file", **args)
    legacy = first.model_copy(update={"content": first.display_content, "display_content": None})
    fingerprint = digest(json.dumps(["write_file", args], sort_keys=True, ensure_ascii=True))
    service.storage.complete(called, fingerprint, legacy)
    replay = await invoke(service, called, "write_file", **args)
    assert "submitted-content" not in replay.content
    assert "+submitted-content" in replay.display_content
    assert (workspace / "old.txt").read_text() == "submitted-content\n"


@pytest.mark.asyncio
async def test_patch_add_delete_duplicate_and_move(coding):
    service, context, workspace = coding
    patch = "*** Begin Patch\n*** Add File: a.txt\n+hello\n*** End Patch"
    result = await invoke(service, context, "apply_patch", patch_text=patch)
    assert result.status == "success" and (workspace / "a.txt").read_text() == "hello\n"
    duplicate = "*** Begin Patch\n*** Delete File: a.txt\n*** Add File: ./a.txt\n+bad\n*** End Patch"
    assert (await invoke(service, context, "apply_patch", patch_text=duplicate)).error.code == "invalid_input"
    move = "*** Begin Patch\n*** Update File: a.txt\n*** Move to: b.txt\n@@\n-hello\n+bye\n*** End Patch"
    assert (await invoke(service, context, "apply_patch", patch_text=move)).error.code == "invalid_input"
    assert (await invoke(service, context, "apply_patch", patch_text="*** Begin Patch\n*** Delete File: a.txt\n*** End Patch")).status == "success"


@pytest.mark.asyncio
async def test_journal_prevents_reexecution_and_unknown_outcomes(coding):
    service, context, workspace = coding
    called = replace(context, tool_call_id="write-1")
    first = await invoke(service, called, "write_file", file_path="a.txt", content="once", append=True)
    second = await invoke(service, called, "write_file", file_path="a.txt", content="once", append=True)
    assert first == second and (workspace / "a.txt").read_text() == "once"
    changed = await invoke(service, called, "write_file", file_path="a.txt", content="different")
    assert changed.error.code == "invalid_input"
    uncertain = replace(context, tool_call_id="unknown")
    import json
    from agent.modules.tools.coding.storage import digest
    args = {"file_path": "a.txt", "content": "must not append", "append": True}
    fingerprint = digest(json.dumps(["write_file", args], sort_keys=True, ensure_ascii=True))
    service.storage.begin(uncertain, fingerprint)
    result = await invoke(service, uncertain, "write_file", **args)
    assert result.error.code == "unknown_outcome"
    assert (workspace / "a.txt").read_text() == "once"


@pytest.mark.asyncio
async def test_process_stdin_cursor_exit_and_thread_isolation(coding):
    service, context, workspace = coding
    result = await invoke(service, context, "exec_command", command=python_command("import sys; print('ready', flush=True); value=sys.stdin.readline(); print(value, end='', flush=True); sys.exit(7)"), yield_time_ms=100)
    assert result.status == "running"
    job = service.processes.get(context, result.data["process_id"])
    foreign = replace(context, thread_id="thread-2")
    with pytest.raises(CodingError):
        service.processes.get(foreign, job.id)
    await service.processes.send(job, "hello\n")
    await asyncio.wait_for(job.finished.wait(), 8)
    observed = await service.processes.observe(job)
    assert "hello" in observed.content and observed.data["exit_code"] == 7
    repeated = await service.processes.observe(job)
    assert repeated.data["cursor"] == observed.data["cursor"]
    empty = await service.processes.observe(job, cursor=observed.data["cursor"])
    assert "hello" not in empty.content


@pytest.mark.asyncio
async def test_process_timeout_and_stop(coding):
    import time
    service, context, workspace = coding
    result = await invoke(service, context, "exec_command", command=python_command("import time; time.sleep(30)"), timeout_seconds=1, yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    await asyncio.wait_for(job.finished.wait(), 8)
    assert (await service.processes.observe(job)).error.code == "timeout"
    result = await invoke(service, context, "exec_command", command=python_command("import time; time.sleep(30)"), yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    started = time.monotonic()
    await asyncio.wait_for(service.processes.stop(job), 8)
    assert time.monotonic() - started < 8
    assert job.process.poll() is not None


@pytest.mark.asyncio
async def test_process_large_output_has_bounded_capture_and_owned_artifact(coding, monkeypatch):
    service, context, workspace = coding
    monkeypatch.setattr("agent.modules.tools.coding.processes.MAX_STORED_BYTES", 128 * 1024)
    result = await invoke(service, context, "exec_command", command=python_command("import sys; sys.stdout.write('x' * (3 * 1024 * 1024))"), yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    await asyncio.wait_for(job.finished.wait(), 10)
    observed = await service.processes.observe(job)
    assert job.stored_bytes <= 128 * 1024
    assert len(job.head) + len(job.tail) <= MAX_CAPTURE_BYTES
    assert observed.capture_truncated and observed.output_truncated
    assert len(observed.content.encode()) <= MAX_MODEL_BYTES
    read = await invoke(service, replace(context, thread_id="foreign"), "read_tool_output", output_ref=job.output_ref)
    assert read.error.code == "not_found"


@pytest.mark.asyncio
async def test_real_tool_node_permission_resume_and_error_status(coding, monkeypatch):
    service, context, workspace = coding
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name:
        SimpleNamespace(tool_permissions=[{"action": "edit", "effect": "ask"}])))
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([make_coding_tool("write_file")]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "graph-thread", "approval_supported": True}}
    raw_context = {"working_dir": str(workspace)}
    message = AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"file_path": "approved.txt", "content": "once", "append": True}, "id": "approved-call"}])
    first = await compiled.ainvoke({"messages": [message]}, config, context=raw_context)
    assert "__interrupt__" in first, first["messages"][-1].content
    request = first["__interrupt__"][0].value
    assert request["type"] == "permission_request" and not (workspace / "approved.txt").exists()
    resumed = await compiled.ainvoke(Command(resume={"action": "permission", "request_id": request["request_id"], "decision": "allow_once"}), config, context=raw_context)
    assert resumed["messages"][-1].status == "success"
    assert resumed["messages"][-1].artifact["data"]["version"]
    assert (workspace / "approved.txt").read_text() == "once"


def test_bounding_is_utf8_safe_and_limits_lines():
    content, truncated = bounded_text("unicode: \U0001f600\n" * 20000, tail=True)
    assert truncated and len(content.encode()) <= MAX_MODEL_BYTES
    assert len(content.splitlines()) <= MAX_MODEL_LINES


@pytest.mark.asyncio
async def test_image_limit_and_binary_detection(coding):
    service, context, workspace = coding
    (workspace / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024))
    assert (await invoke(service, context, "read_file", file_path="large.png")).error.code == "invalid_input"
    (workspace / "binary.bin").write_bytes(b"abc\0def")
    assert (await invoke(service, context, "read_file", file_path="binary.bin")).error.code == "invalid_input"
    (workspace / "small.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"small")
    result = await invoke(service, context, "read_file", file_path="small.png")
    assert result.content[1]["type"] == "image"


@pytest.mark.asyncio
async def test_search_checks_each_file_and_excludes_output_store(coding):
    service, context, workspace = coding
    (workspace / "secret.txt").write_text("secret", encoding="utf-8")
    denied = replace(context, permission_rules=(PermissionRule(action="read", resource="*secret.txt", effect="deny"),))
    result = await invoke(service, denied, "grep", pattern="secret")
    assert result.error.code == "permission_denied"
    internal = CodingService(workspace / "artifacts")
    ref, path = internal.storage.create_output(context)
    path.write_text("private-output", encoding="utf-8")
    result = await invoke(internal, context, "grep", pattern="private-output")
    assert result.data["matches"] == []


@pytest.mark.asyncio
async def test_permission_last_match_and_agent_override(coding, monkeypatch, isolated_container):
    service, context, workspace = coding
    allowed = replace(context, permission_rules=(PermissionRule(action="edit", effect="deny"),
                      PermissionRule(action="edit", resource="*allowed.txt", effect="allow")))
    assert (await invoke(service, allowed, "write_file", file_path="allowed.txt", content="allowed")).status == "success"
    assert (await invoke(service, allowed, "write_file", file_path="denied.txt", content="denied")).error.code == "permission_denied"
    original_get = isolated_container.config_service.get
    monkeypatch.setattr(isolated_container.config_service, "get", lambda key, default=None:
        [{"action": "edit", "effect": "deny"}] if key == "tools.permissions" else original_get(key, default))
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name:
        SimpleNamespace(tool_permissions=[])))
    from agent.modules.tools.coding.adapter import invocation_context
    runtime = SimpleNamespace(context={"working_dir": str(workspace)}, config={"configurable": {"thread_id": "t"}}, tool_call_id="id")
    assert invocation_context(runtime).permission_rules == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["allow_thread", "deny"])
async def test_permission_thread_grant_and_denial(coding, decision):
    service, context, workspace = coding
    context = replace(context, tool_call_id="approval", approval_supported=True,
                      permission_rules=(PermissionRule(action="edit", effect="ask"),))
    graph = StateGraph(dict)
    async def node(state):
        result = await invoke(service, context, "write_file", file_path="approved.txt", content="approved")
        return {"status": result.status}
    graph.add_node("write", node)
    graph.add_edge(START, "write")
    graph.add_edge("write", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "approval-test"}}
    first = await compiled.ainvoke({}, config)
    request = first["__interrupt__"][0].value
    resumed = await compiled.ainvoke(Command(resume={"action": "permission", "request_id": request["request_id"], "decision": decision}), config)
    assert resumed["status"] == ("success" if decision == "allow_thread" else "error")
    assert (workspace / "approved.txt").exists() == (decision == "allow_thread")
    if decision == "allow_thread":
        granted = replace(context, tool_call_id="next-call")
        assert (await invoke(service, granted, "write_file", file_path="approved.txt", content="next")).status == "success"
        other_thread = replace(granted, thread_id="other", approval_supported=False)
        assert (await invoke(service, other_thread, "write_file", file_path="approved.txt", content="forbidden")).error.code == "approval_unavailable"
        service.storage.clear_thread_grants(context.thread_id)
        assert (await invoke(service, replace(granted, tool_call_id="after-cleanup", approval_supported=False), "write_file", file_path="approved.txt", content="forbidden")).error.code == "approval_unavailable"


@pytest.mark.asyncio
async def test_batch_approval_before_any_effect_and_multiple_resumes(coding, monkeypatch):
    service, context, workspace = coding
    from agent.modules.tools import ToolResolver
    from agent.modules.workflows.nodes.tool import tool_node
    async def resolve(self, *args, **kwargs):
        return [make_coding_tool("write_file")]
    monkeypatch.setattr(ToolResolver, "aresolve_for_agent", resolve)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name:
        SimpleNamespace(tool_permissions=[{"action": "edit", "effect": "ask"}])))
    graph = StateGraph(MessagesState)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "batch", "approval_supported": True}}
    raw_context = {"working_dir": str(workspace)}
    message = AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"file_path": f"{index}.txt", "content": "once", "append": True}, "id": f"batch-{index}"} for index in range(2)])
    result = await compiled.ainvoke({"messages": [message]}, config, context=raw_context)
    for index in range(2):
        assert not list(workspace.glob("*.txt"))
        request = result["__interrupt__"][0].value
        result = await compiled.ainvoke(Command(resume={"action": "permission", "request_id": request["request_id"], "decision": "allow_once"}), config, context=raw_context)
    assert "__interrupt__" not in result
    assert len(result["messages"]) == 3
    assert all((workspace / f"{index}.txt").read_text() == "once" for index in range(2))


@pytest.mark.asyncio
async def test_new_file_race_and_replaced_symlink_refuse_commit(coding, monkeypatch):
    service, context, workspace = coding
    target = workspace / "created.txt"
    original_link = os.link
    def create_before_link(source, destination):
        Path(destination).write_text("concurrent", encoding="utf-8")
        return original_link(source, destination)
    monkeypatch.setattr(os, "link", create_before_link)
    result = await invoke(service, context, "write_file", file_path="created.txt", content="overwrite")
    assert result.error.code == "stale_content" and target.read_text() == "concurrent"


@pytest.mark.asyncio
async def test_process_unicode_long_command_and_no_newline(coding):
    service, context, workspace = coding
    command = python_command("import sys; sys.stdout.write('unicode: \U0001f600'); sys.stdout.flush()") + "\n#" + "x" * 9000
    result = await invoke(service, context, "exec_command", command=command, yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    await asyncio.wait_for(job.finished.wait(), 10)
    assert "unicode: \U0001f600" in (await service.processes.observe(job)).content
    assert not list(service.storage.owner_dir(context).glob("*.ps1"))


@pytest.mark.asyncio
async def test_output_cursors_preserve_utf8_and_all_retained_lines(coding):
    service, context, workspace = coding
    result = await invoke(service, context, "exec_command", command=python_command("import sys; sys.stdout.write('line\U0001f600\\n' * 10000)"), yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    await asyncio.wait_for(job.finished.wait(), 10)
    cursor = 0
    pieces = []
    while cursor < job.stored_bytes:
        page = await service.processes.observe(job, cursor=cursor)
        assert page.data["cursor"] > cursor
        assert len(page.content.splitlines()) <= MAX_MODEL_LINES
        pieces.append(page.content.split("\n\nProcess ", 1)[0])
        cursor = page.data["cursor"]
    assert "".join(pieces).count("\U0001f600") == 10000
    preview = await service.processes.observe(job, preview=True)
    assert preview.data["tail_preview"] and "tail preview" in preview.content


@pytest.mark.asyncio
@pytest.mark.parametrize("orphan", [False, True])
async def test_process_tree_cleanup_including_exited_parent(coding, orphan):
    import time
    import psutil
    service, context, workspace = coding
    source = "import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],stdout=sys.stdout,stderr=sys.stderr); Path('child.pid').write_text(str(child.pid)); print('child-ready',flush=True)"
    if not orphan:
        source += "; time.sleep(60)"
    result = await invoke(service, context, "exec_command", command=python_command(source), yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    try:
        started = time.monotonic()
        for _ in range(100):
            if (workspace / "child.pid").exists():
                break
            await asyncio.sleep(0.05)
        pid = int((workspace / "child.pid").read_text())
        if not orphan:
            await service.processes.stop(job)
        await asyncio.wait_for(job.finished.wait(), 10)
        assert time.monotonic() - started < 10
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    finally:
        await service.processes.stop(job)


@pytest.mark.asyncio
async def test_thread_cleanup_and_output_retention(coding):
    import time
    service, context, workspace = coding
    result = await invoke(service, context, "exec_command", command=python_command("import time; time.sleep(30)"), yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    assert service.processes.stop_thread_now(context.thread_id) == 1
    await asyncio.wait_for(job.finished.wait(), 8)
    with pytest.raises(CodingError):
        service.processes.get(context, job.id)
    ref, path = service.storage.create_output(context)
    path.write_text("old output", encoding="utf-8")
    os.utime(path, (time.time() - 8 * 86400,) * 2)
    service.storage.cleanup()
    assert not path.exists()
    with pytest.raises(CodingError):
        service.storage.output_path(context, "../escape")


def test_all_workspace_backends_use_one_coding_toolset(coding):
    _, _, workspace = coding
    from agent.modules.tools import ToolResolver
    resolver = ToolResolver()
    tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="bash"), make_coding_tool("exec_command")]
    for ref in (str(workspace), {"backend": "daytona", "locator": "sandbox"},
                {"backend": "modal", "locator": "sandbox"}):
        resolved = resolver.for_workspace(tools, ref)
        assert [tool.name for tool in resolved] == ["read_file", "exec_command"]
        assert all(getattr(tool, "coding_definition", None) for tool in resolved)


@pytest.mark.asyncio
async def test_windows_powershell_fallback_preserves_unicode_and_native_exit(coding):
    import shutil
    if os.name != "nt" or not (shell := shutil.which("powershell.exe")):
        pytest.skip("Windows PowerShell fallback is unavailable.")
    service, context, workspace = coding
    service.shell = shell
    result = await invoke(service, context, "exec_command", command=python_command("import sys; print('emoji\U0001f600',flush=True); sys.exit(9)"), yield_time_ms=0)
    job = service.processes.get(context, result.data["process_id"])
    await asyncio.wait_for(job.finished.wait(), 10)
    output = await service.processes.observe(job)
    assert "emoji\U0001f600" in output.content and output.data["exit_code"] == 9
