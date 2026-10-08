"""Checkpointed skill instructions remain effective across compaction and resume."""

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import sys

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
import pytest

from agent.modules.skills.context import active_context, model_skill_history, skill_commands, skill_events
from agent.modules.skills.packages import SkillPackages
from agent.modules.skills.repository import FilesystemSkillRepository
from agent.modules.workflows.state.base import BaseState
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs


@pytest.fixture
def setup(tmp_path, isolated_container):
    root = tmp_path / "skills"
    directory = root / "demo"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: demo\ndescription: A skill.\n---\nRetain these durable instructions.\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolated_container._skill_repository = FilesystemSkillRepository(root)
    service = SkillPackages(isolated_container.skill_repository)
    isolated_container._skill_packages = service
    options = {"workspace": str(workspace), "thread_id": "thread", "agent_name": "default", "names": None, "tools": [SimpleNamespace(name="skill")]}
    return service, options


@pytest.mark.asyncio
async def test_compaction_preserves_active_skill_checkpoint(setup, monkeypatch):
    service, options = setup

    async def prepare(state):
        content, active, processed = await active_context(state, **options)
        return {"active_skills": active, "processed_skill_messages": processed}

    builder = StateGraph(BaseState)
    builder.add_node("prepare", prepare)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    first = await graph.ainvoke({"messages": [HumanMessage(content="/skill demo", id="user-1"), AIMessage(content="Ready"), HumanMessage(content="Continue"), AIMessage(content="Done")]}, config)
    assert len(first["active_skills"]) == 0
    loaded = await graph.ainvoke({"messages": [HumanMessage(content="/skill demo", id="user-2")]}, config)
    assert len(loaded["active_skills"]) == 1
    from agent.modules.conversations import compaction

    async def summarize(*args, **kwargs):
        return "The user asked to continue."

    monkeypatch.setattr(compaction, "_generate_context_summary", summarize)
    compacted = await compaction.compact_message_history(loaded["messages"], keep_recent_messages=2)
    await graph.aupdate_state(config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *compacted.messages]}, as_node="prepare")
    checkpoint = await graph.aget_state(config)
    assert len(checkpoint.values["active_skills"]) == 1
    content, active, _ = await active_context(checkpoint.values, **options)
    assert "Retain these durable instructions." in content
    assert len(active) == 1
    # The original load directive must not reactivate a skill after unloading.
    unloaded = await graph.ainvoke({"messages": [HumanMessage(content="/skill unload demo", id="user-3")]}, config)
    assert unloaded["active_skills"] == {}


@pytest.mark.asyncio
async def test_active_skill_permissions_and_scope_checked_on_resume(setup):
    service, options = setup
    state = {"messages": [HumanMessage(content="/skill demo", id="user-1")]}
    _, active, processed = await active_context(state, **options)
    resumed = {"messages": [HumanMessage(content="Continue", id="user-2")], "active_skills": active, "processed_skill_messages": processed}
    for overrides in ({"agent_name": "other"}, {"names": []}, {"tools": []}):
        content, retained, _ = await active_context(resumed, **{**options, **overrides})
        assert not content and retained == {}
    package = (await service.inventory())[0]
    await service.enabled(package.id, False)
    content, retained, _ = await active_context(resumed, **options)
    assert not content and retained == {}
    assert len(resumed["active_skills"]) == 1


@pytest.mark.asyncio
async def test_pinned_project_skill_survives_missing_source_and_snapshot(setup):
    service, options = setup
    project = Path(options["workspace"]) / ".agents" / "skills" / "project-demo"
    project.mkdir(parents=True)
    (project / "SKILL.md").write_text("---\nname: project-demo\ndescription: Project skill.\n---\nPinned project instructions.\n")
    _, active, processed = await active_context({"messages": [HumanMessage(content="/skill project-demo", id="user-1")]}, **options)
    entry = next(iter(active.values()))
    shutil.rmtree(project)
    shutil.rmtree(entry["skill_root"])
    resumed = {"active_skills": active, "processed_skill_messages": processed, "messages": [HumanMessage(content="Continue", id="user-2")]}
    content, _, _ = await active_context(resumed, **options)
    assert "Pinned project instructions." in content
    assert Path(entry["skill_root"], "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_switching_scope_does_not_replay_an_old_activation_command(setup, tmp_path):
    service, options = setup
    messages = [HumanMessage(content="/skill demo", id="user-1")]
    _, active, processed = await active_context({"messages": messages}, **options)
    resumed = {"messages": messages, "active_skills": active, "processed_skill_messages": processed}
    content, retained, _ = await active_context(resumed, **{**options, "agent_name": "other"})
    assert not content and retained == {}
    workspace = tmp_path / "other-workspace"
    workspace.mkdir()
    content, retained, _ = await active_context(resumed, **{**options, "workspace": str(workspace)})
    assert not content and retained == {}
    assert not (workspace / ".k41-agent" / "skills").exists()


@pytest.mark.parametrize("command,expected", [
    ("/skill demo", [("load", "demo")]),
    ("/skill load demo", [("load", "demo")]),
    ("/skill unload demo", [("unload", "demo")]),
    ("/skill refresh demo", [("refresh", "demo")]),
    ("/SKILL LOAD demo", [("load", "demo")]),
    ("/Skill Unload demo", [("unload", "demo")]),
    ("/SKILL REFRESH demo", [("refresh", "demo")]),
    ("/skill demo Process the input", [("load", "demo")]),
    ("/skill load demo Process the input", [("load", "demo")]),
    ("/skill demo\n/SKILL load demo\n/skill unload demo\nTask", [("load", "demo"), ("unload", "demo")]),
    ("Task\n/skill demo", []),
    (" /skill demo", []),
])
def test_skill_commands_parse_explicit_verbs_and_case(command, expected):
    commands, message_id = skill_commands([HumanMessage(content=command, id="command")])
    assert commands == expected
    assert message_id == "command"


@pytest.mark.parametrize("command", ["/skill", "/skill load", "/skill unload", "/skill refresh", "/SKILL LOAD"])
def test_skill_commands_require_a_name(command):
    with pytest.raises(ValueError, match="Skill name is required"):
        skill_commands([HumanMessage(content=command)])


@pytest.mark.asyncio
async def test_explicit_load_and_uppercase_commands_activate_and_unload(setup):
    _, options = setup
    _, active, processed = await active_context({"messages": [HumanMessage(content="/SKILL LOAD demo", id="load")]}, **options)
    assert [entry["name"] for entry in active.values()] == ["demo"]
    content, active, processed = await active_context({"messages": [HumanMessage(content="/SKILL UNLOAD demo", id="unload")],
        "active_skills": active, "processed_skill_messages": processed}, **options)
    assert content == "" and active == {} and processed == ["load", "unload"]


@pytest.mark.asyncio
async def test_coding_read_can_access_activated_snapshot_and_enforces_thread_scope(setup, tmp_path):
    from agent.modules.tools.coding.adapter import SCHEMAS
    from agent.modules.tools.coding.models import InvocationContext, ToolDefinition
    from agent.modules.tools.coding.service import CodingService
    service, options = setup
    loaded = await service.activate("demo", **{key: value for key, value in options.items() if key not in {"tools", "names"}})
    coding = CodingService(tmp_path / "output")

    async def execute(parsed, context, runtime):
        return await runtime.execute("read", parsed.model_dump(), context)

    definition = ToolDefinition("read", "read", SCHEMAS["read"], execute)
    context = InvocationContext("default", options["workspace"], "thread", skill_roots=(loaded.skill_root,), skill_cache_root=str(service.cache_root))
    result = await coding.invoke(definition, {"file_path": str(Path(loaded.skill_root) / "SKILL.md")}, context)
    assert result.status == "success", result.content
    assert "Retain these durable instructions." in result.content
    other_context = InvocationContext("default", options["workspace"], "other-thread", skill_cache_root=str(service.cache_root))
    result = await coding.invoke(definition, {"file_path": str(Path(loaded.skill_root) / "SKILL.md")}, other_context)
    assert result.status == "error" and result.error.code == "not_found"


def test_model_projection_uses_receipt_without_losing_checkpoint_payload():
    original = ToolMessage(content="Long durable instructions", name="skill", tool_call_id="call-1", artifact={"kind": "skill_activation", "key": "key", "action": "load", "skill": {"name": "demo", "content": "Full instructions"}})
    active = skill_events([original])
    projected = model_skill_history([original])[0]
    assert "Full instructions" == active["key"]["content"]
    assert projected.artifact is None and "Long durable instructions" not in projected.content
    assert original.artifact is not None


@pytest.mark.asyncio
async def test_actual_skill_tool_persists_and_refreshes_with_langgraph(setup):
    from agent.modules.tools.builtin.skill.skill import skill
    from agent.modules.workflows.nodes.tool import tool_node
    from agent.modules.workflows.run_config import WorkflowContext

    service, options = setup
    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    context = WorkflowContext(workspace=options["workspace"], allowed_tool_names=["skill"])

    async def invoke(arguments, call_id):
        return await graph.ainvoke({"messages": [AIMessage(content="", tool_calls=[
            {"name": "skill", "args": arguments, "id": call_id, "type": "tool_call"}])]}, config, context=context)

    loaded = await invoke({"name": "demo"}, "load-1")
    message = loaded["messages"][-1]
    assert message.status == "success", message.content
    assert message.artifact["kind"] == "skill_activation"
    first = next(iter(loaded["active_skills"].values()))
    document = service.repository.root / "demo" / "SKILL.md"
    document.write_text(document.read_text() + "Updated instructions.\n")
    repeated = await invoke({"name": "demo"}, "load-2")
    assert next(iter(repeated["active_skills"].values()))["version"] == first["version"]
    refreshed = await invoke({"name": "demo", "refresh": True}, "load-3")
    assert next(iter(refreshed["active_skills"].values()))["version"] != first["version"]
    unloaded = await invoke({"name": "demo", "action": "unload"}, "unload-1")
    assert unloaded["active_skills"] == {}


@pytest.mark.asyncio
async def test_skill_load_requires_a_subsequent_resource_batch(setup):
    from agent.modules.workflows.nodes.tool import tool_node
    from agent.modules.workflows.run_config import WorkflowContext

    service, options = setup
    loaded = await service.activate("demo", workspace=options["workspace"], thread_id="thread", agent_name="default")
    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    context = WorkflowContext(workspace=options["workspace"], allowed_tool_names=["skill", "read"])
    read_args = {"file_path": str(Path(loaded.skill_root) / "SKILL.md")}

    async def invoke(calls):
        return await graph.ainvoke({"messages": [AIMessage(content="", tool_calls=calls)]}, config, context=context)

    blocked = await invoke([
        {"name": "skill", "args": {"name": "demo"}, "id": "batch-load", "type": "tool_call"},
        {"name": "read", "args": read_args, "id": "batch-read", "type": "tool_call"},
    ])
    assert not blocked.get("active_skills")
    for message in blocked["messages"][-2:]:
        assert message.status == "error"
        assert message.artifact["data"]["batch_stopped_before_execution"]
    activated = await invoke([{"name": "skill", "args": {"name": "demo"}, "id": "load", "type": "tool_call"}])
    assert len(activated["active_skills"]) == 1
    read = await invoke([{"name": "read", "args": read_args, "id": "read", "type": "tool_call"}])
    assert read["messages"][-1].status == "success"
    assert "Retain these durable instructions." in read["messages"][-1].content


@pytest.mark.asyncio
async def test_actual_uv_script_uses_snapshot_resources_without_changing_source(setup):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is required for this integration test")
    service, options = setup
    source = service.repository.root / "demo"
    (source / "assets").mkdir()
    (source / "assets" / "template.bin").write_bytes(b"\x00\xff\x01")
    (source / "scripts").mkdir()
    script = source / "scripts" / "run.py"
    script.write_text("# /// script\n# dependencies = []\n# ///\nfrom pathlib import Path\nimport sys\nroot = Path(__file__).parent.parent\nPath(sys.argv[1]).write_bytes((root / 'assets' / 'template.bin').read_bytes())\n")
    initial = script.read_bytes()
    activated = await service.activate("demo", **{key: value for key, value in options.items() if key not in {"tools", "names"}})
    output = Path(options["workspace"]) / "output.bin"
    result = subprocess.run([uv, "run", "--offline", "--no-project", "--python", sys.executable, "--script", str(Path(activated.skill_root) / "scripts" / "run.py"), str(output)],
                            cwd=options["workspace"], capture_output=True, timeout=30, **hidden_subprocess_kwargs())
    assert result.returncode == 0, result.stderr.decode()
    assert output.read_bytes() == b"\x00\xff\x01"
    assert script.read_bytes() == initial
    assert not (Path(options["workspace"]) / ".venv").exists()
