"""ACP agent graph driven through the real runner against the fake agent."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from agent.modules.acp.errors import AcpNotInstalled
from agent.modules.acp.process import AgentCommand
from agent.modules.acp.service import AcpService, not_installed_notice
from agent.modules.acp.turns import SIGN_IN_NOTICE, STOP_TURN_OPTION_ID
from agent.modules.agent_runtime import runner as runner_module
from agent.modules.agents.models import AcpAgentSettings, AgentConfig
from agent.modules.agents.parser import parse_agent_markdown_content, serialize_agent_config
from agent.modules.tools.coding.models import PermissionResume
from agent.modules.workflows.graphs.acp_agent import TURN_LOST_NOTICE, compile_acp_agent_graph

FAKE_AGENT = Path(__file__).parent / "fixtures" / "acp" / "fake_agent.py"
THREAD = "api_dashboard_acp-thread"


class Env:
    def __init__(self, tmp_path, container, monkeypatch, graph):
        self.state_dir = tmp_path / "fake-state"
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        self.container = container
        self.monkeypatch = monkeypatch
        self.graph = graph
        self.flags: list[str] = []
        self.agent = AgentConfig(name="ag", graph_type="acp_agent", acp=AcpAgentSettings())
        self.service = self.new_service()

    def new_service(self) -> AcpService:
        service = AcpService(command_resolver=lambda _agent: AgentCommand(
            argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(self.state_dir), *self.flags],
            cwd=self.state_dir,
        ))
        self.container._acp_service = service
        return service

    async def restart_server(self) -> None:
        await self.service.close()
        self.service = self.new_service()

    def stream(self, text="", *, thread=THREAD, platform="api", working_dir=None, **kwargs):
        return runner_module.run_agent_stream(
            user_input=text, thread_id=thread, agent_name="ag", working_dir=str(working_dir or self.workspace),
            usage_context={"platform": platform}, **kwargs,
        )

    async def collect(self, text="", **kwargs) -> list[dict]:
        async def consume():
            return [event async for event in self.stream(text, **kwargs)]

        return await asyncio.wait_for(consume(), 30)

    async def state(self, thread=THREAD):
        return await self.graph.aget_state({"configurable": {"thread_id": thread}})

    async def messages(self, thread=THREAD):
        return (await self.state(thread)).values["messages"]

    def calls(self, method: str) -> list[dict]:
        path = self.state_dir / "calls.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row.get("method") == method]

    def effects(self) -> list[str]:
        path = self.state_dir / "effects.log"
        return path.read_text(encoding="utf-8").split() if path.exists() else []


@pytest_asyncio.fixture
async def env(tmp_path, isolated_container, monkeypatch):
    graph = compile_acp_agent_graph(InMemorySaver())
    environment = Env(tmp_path, isolated_container, monkeypatch, graph)
    monkeypatch.setattr(runner_module, "get_workflow_graph", lambda name: graph)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(
        get_agent=lambda name: environment.agent, get_card=lambda name: None,
    ))

    async def no_prompt_variables(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("agent.modules.prompt_variables.get_runtime_prompt_variable_values", no_prompt_variables)

    async def no_record(**_kwargs):
        return None

    monkeypatch.setattr(runner_module, "_record_conversation_thread", no_record)
    yield environment
    await environment.service.close()


def types(events):
    return [event["type"] for event in events]


def texts(events):
    return "".join(event["content"] for event in events if event["type"] == "message")


def request_of(events):
    return next(event for event in events if event["type"] == "user_input_request")


def question_of(events):
    return request_of(events)["questions"][0]


def resume(events, option_id, *, request_id=None):
    identifier = request_id or question_of(events)["id"].split(":", 1)[1]
    return {"action": "acp_permission", "request_id": identifier, "option_id": option_id}


def visible(messages):
    """History as the dashboard reloads it: (role, text-or-name)."""
    rows = []
    for message in messages:
        if isinstance(message, HumanMessage):
            rows.append(("user", message.content))
        elif isinstance(message, ToolMessage):
            rows.append(("tool", message.content))
        elif isinstance(message, AIMessage) and message.tool_calls:
            rows.append(("call", message.tool_calls[0]["name"]))
        elif isinstance(message, AIMessage):
            rows.append(("ai", message.content))
    return rows


# Text and tools ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_turn_streams_once_and_persists(env):
    events = await env.collect("scenario:text")
    assert texts(events) == "Hello world"
    finals = [event for event in events if event["type"] == "final"]
    assert finals == [{"type": "final", "content": "Hello world", "already_streamed": True}]
    assert next(event for event in events if event["type"] == "context_usage")["current_context_tokens"] == 123
    assert visible(await env.messages()) == [("user", "scenario:text"), ("ai", "Hello world")]
    assert not (await env.state()).next


@pytest.mark.asyncio
async def test_tool_events_appear_once_and_persist_in_order(env):
    events = await env.collect("scenario:tools")
    assert [event["type"] for event in events if event["type"] != "context_usage"] == [
        "message", "tool_call", "tool_result", "message", "final",
    ]
    call = next(event for event in events if event["type"] == "tool_call")
    result = next(event for event in events if event["type"] == "tool_result")
    assert call["name"] == "acp_execute" and call["args"]["title"] == "echo hi"
    assert result["tool_call_id"] == call["id"] and result["content"] == "hi\r\n"
    assert visible(await env.messages()) == [
        ("user", "scenario:tools"), ("ai", "Running."), ("call", "acp_execute"), ("tool", "hi\r\n"), ("ai", "Done."),
    ]


@pytest.mark.asyncio
async def test_turn_ending_on_a_tool_still_gives_a_final_message(env):
    events = await env.collect("scenario:ends_on_tool")
    assert events[-1]["type"] == "final" and events[-1]["content"]
    assert isinstance((await env.messages())[-1], AIMessage)


@pytest.mark.asyncio
async def test_non_spec_stop_reason_finishes_normally(env):
    events = await env.collect("scenario:bad_stop")
    assert events[-1] == {"type": "final", "content": "Quota message.", "already_streamed": True}


@pytest.mark.asyncio
async def test_non_streaming_path_returns_the_final_text(env):
    result = await asyncio.wait_for(runner_module.run_agent_full(
        "scenario:text", THREAD, "ag", working_dir=str(env.workspace), usage_context={"platform": "api"},
    ), 30)
    assert result == "Hello world"


# Permissions ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_transcript_survives_a_pending_permission(env):
    events = await env.collect("scenario:permission")
    assert types(events)[-1] == "user_input_request"
    assert (await env.state()).next == ("acp_permission",)
    assert visible(await env.messages()) == [("user", "scenario:permission"), ("ai", "Before. ")]
    assert env.effects() == []


@pytest.mark.asyncio
async def test_card_offers_agent_options_and_stop(env):
    events = await env.collect("scenario:permission")
    question = question_of(events)
    assert [option["id"] for option in question["options"]] == ["allow", "deny", STOP_TURN_OPTION_ID]
    assert "Run create_file?" in question["question"] and "hello.txt" in question["question"]
    assert question["free_text"]["enabled"] is False


@pytest.mark.asyncio
async def test_allow_runs_the_operation_and_hides_the_noise_call(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert env.effects() == ["edit-1"]
    assert texts(second) == "After."
    assert [event["name"] for event in second if event["type"] == "tool_call"] == ["acp_edit"]
    assert second[-1]["type"] == "final"
    assert visible(await env.messages()) == [
        ("user", "scenario:permission"), ("ai", "Before. "), ("call", "acp_edit"), ("tool", "Create hello.txt"), ("ai", "After."),
    ]


@pytest.mark.asyncio
async def test_deny_prevents_the_operation_and_shows_a_failed_tool(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, "deny"))
    assert env.effects() == []
    assert next(event for event in second if event["type"] == "tool_result")["content"] == "Rejected by user"


@pytest.mark.asyncio
@pytest.mark.parametrize("forged", ["allow_always", "__anything__"])
async def test_option_that_was_not_offered_authorizes_nothing(env, forged):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, forged))
    assert env.effects() == []
    assert types(second)[-1] == "user_input_request"
    third = await env.collect(resume_payload=resume(second, "allow"))
    assert env.effects() == ["edit-1"] and third[-1]["type"] == "final"


@pytest.mark.asyncio
async def test_resume_for_another_request_authorizes_nothing(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, "allow", request_id="unrelated"))
    assert env.effects() == [] and types(second)[-1] == "user_input_request"


@pytest.mark.asyncio
async def test_stop_option_ends_the_turn_and_keeps_output(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, STOP_TURN_OPTION_ID))
    assert env.effects() == [] and not (await env.state()).next
    assert second[-1] == {"type": "final", "content": "Before. ", "already_streamed": True}
    assert visible(await env.messages()) == [("user", "scenario:permission"), ("ai", "Before. ")]


@pytest.mark.asyncio
async def test_sequential_permissions_each_pause(env):
    first = await env.collect("scenario:two_permissions")
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert types(second)[-1] == "user_input_request" and env.effects() == ["edit-1"]
    third = await env.collect(resume_payload=resume(second, "allow"))
    assert env.effects() == ["edit-1", "edit-2"]
    assert third[-1]["content"] == "Three."
    assert [text for role, text in visible(await env.messages()) if role == "ai"] == ["One. ", "Two. ", "Three."]


@pytest.mark.asyncio
async def test_concurrent_permissions_are_asked_one_at_a_time(env):
    first = await env.collect("scenario:concurrent_permissions")
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert types(second)[-1] == "user_input_request"
    assert question_of(second)["id"] != question_of(first)["id"]
    third = await env.collect(resume_payload=resume(second, "deny"))
    answers = json.loads(third[-1]["content"])
    assert sorted(answers.values()) == ["allow", "deny"]


@pytest.mark.asyncio
async def test_surface_without_approval_denies_automatically(env):
    events = await env.collect("scenario:permission", thread="telegram_user_chat", platform="telegram")
    assert "user_input_request" not in types(events)
    assert env.effects() == []
    assert events[-1]["type"] == "final" and "After." in events[-1]["content"]


@pytest.mark.asyncio
async def test_non_streaming_path_denies_instead_of_hanging(env):
    result = await asyncio.wait_for(runner_module.run_agent_full(
        "scenario:permission", THREAD, "ag", working_dir=str(env.workspace), usage_context={"platform": "api"},
    ), 30)
    assert result == "After." and env.effects() == []


@pytest.mark.asyncio
async def test_auto_allow_card_never_pauses(env):
    env.agent = AgentConfig(name="ag", graph_type="acp_agent", acp=AcpAgentSettings(permission_mode="auto_allow"))
    events = await env.collect("scenario:permission")
    assert "user_input_request" not in types(events) and env.effects() == ["edit-1"]


def test_acp_resume_cannot_pass_as_a_coding_approval():
    with pytest.raises(ValidationError):
        PermissionResume.model_validate({"action": "acp_permission", "request_id": "x", "option_id": "allow"})


@pytest.mark.asyncio
async def test_pending_request_is_restored_after_a_reload(env, monkeypatch):
    from agent.modules.tools import pending_permission_requests

    first = await env.collect("scenario:permission")

    async def thread_record(_thread_id):
        return {"agent_name": "ag"}

    monkeypatch.setattr("agent.modules.conversations.get_conversation_thread", thread_record)
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda name: {"acp_agent": env.graph}[name])
    restored = await pending_permission_requests(THREAD)
    assert restored[0]["questions"][0]["id"] == question_of(first)["id"]


# Stopping ---------------------------------------------------------------------


async def start_slow_turn(env):
    queue: asyncio.Queue = asyncio.Queue()

    async def consume():
        async for event in env.stream("scenario:slow"):
            await queue.put(event)

    task = asyncio.create_task(consume())
    while True:
        event = await asyncio.wait_for(queue.get(), 20)
        if event["type"] == "message":
            return task, queue


@pytest.mark.asyncio
async def test_graceful_stop_commits_partial_output_and_ends_the_run(env):
    task, _queue = await start_slow_turn(env)
    assert await env.service.stop_thread(THREAD) is True
    await asyncio.wait_for(task, 10)
    state = await env.state()
    assert not state.next
    last = state.values["messages"][-1]
    assert isinstance(last, AIMessage) and last.content.startswith("w0")
    assert "cancelled by the client" not in last.content


@pytest.mark.asyncio
async def test_hard_cancel_then_continue_never_resends_the_prompt(env):
    task, _queue = await start_slow_turn(env)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await env.state()).next == ("acp_turn",)
    events = await env.collect(resume=True)
    assert len(env.calls("session/prompt")) == 1
    assert events[-1]["type"] == "final"
    assert isinstance((await env.messages())[-1], AIMessage)


@pytest.mark.asyncio
async def test_stop_while_paused_then_answer_commits_partial_output(env):
    first = await env.collect("scenario:permission")
    assert await env.service.stop_thread(THREAD) is True
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert env.effects() == [] and not (await env.state()).next
    assert second[-1]["content"] == "Before. "


@pytest.mark.asyncio
async def test_new_message_during_a_pause_ends_the_old_turn_first(env):
    await env.collect("scenario:permission")
    await env.collect("second message")
    assert env.effects() == []
    assert len(env.calls("session/prompt")) == 2
    assert len(env.calls("session/new")) == 1
    assert visible(await env.messages())[-1] == ("ai", "second message")
    assert not (await env.state()).next


# Restart and sessions ---------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_while_paused_reports_a_lost_turn_without_resending(env):
    first = await env.collect("scenario:permission")
    await env.restart_server()
    events = await env.collect(resume_payload=resume(first, "allow"))
    assert TURN_LOST_NOTICE in texts(events)
    assert len(env.calls("session/prompt")) == 1 and env.effects() == []
    assert not (await env.state()).next


@pytest.mark.asyncio
async def test_restart_mid_turn_then_continue_reports_a_lost_turn(env):
    task, _queue = await start_slow_turn(env)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await env.restart_server()
    events = await env.collect(resume=True)
    assert TURN_LOST_NOTICE in texts(events)
    assert len(env.calls("session/prompt")) == 1


@pytest.mark.asyncio
async def test_second_message_reuses_the_live_session(env):
    await env.collect("one")
    await env.collect("two")
    assert len(env.calls("session/new")) == 1 and not env.calls("session/resume")
    prompts = env.calls("session/prompt")
    assert prompts[0]["params"]["sessionId"] == prompts[1]["params"]["sessionId"]


@pytest.mark.asyncio
async def test_session_resumes_after_a_restart(env):
    await env.collect("one")
    await env.restart_server()
    events = await env.collect("two")
    assert len(env.calls("session/new")) == 1 and len(env.calls("session/resume")) == 1
    assert texts(events) == "two"


@pytest.mark.asyncio
async def test_load_replay_is_not_shown_again(env):
    await env.collect("one")
    await env.restart_server()
    env.flags = ["--no-resume"]
    events = await env.collect("two")
    assert len(env.calls("session/load")) == 1 and texts(events) == "two"
    assert "old reply" not in str(await env.messages())


@pytest.mark.asyncio
async def test_unrestorable_session_starts_fresh_with_a_notice(env):
    await env.collect("one")
    await env.restart_server()
    for marker in env.state_dir.glob("session-*"):
        marker.unlink()
    events = await env.collect("two")
    assert "could not be restored" in texts(events) and texts(events).endswith("two")
    assert len(env.calls("session/new")) == 2


@pytest.mark.asyncio
async def test_changed_workspace_starts_a_new_session_with_a_notice(env, tmp_path):
    await env.collect("one")
    other = tmp_path / "other"
    other.mkdir()
    events = await env.collect("two", working_dir=other)
    assert "workspace changed" in texts(events)
    sessions = env.calls("session/new")
    assert len(sessions) == 2 and sessions[1]["params"]["cwd"] == str(other.resolve())


@pytest.mark.asyncio
async def test_missing_thread_workspace_is_created_before_the_session(env, tmp_path):
    missing = tmp_path / "not-yet" / "created"
    await env.collect("hello", working_dir=missing)
    assert missing.is_dir()
    assert env.calls("session/new")[0]["params"]["cwd"] == str(missing.resolve())


# Failures ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_keeps_partial_output_and_next_message_recovers(env):
    events = await env.collect("scenario:crash")
    assert "stopped unexpectedly" in texts(events)
    assert visible(await env.messages())[1] == ("ai", "Partial. ")
    following = await env.collect("after the crash")
    assert texts(following) == "after the crash"


@pytest.mark.asyncio
async def test_missing_binary_is_reported_in_the_conversation(env):
    def not_installed(agent_id):
        raise AcpNotInstalled(not_installed_notice(agent_id))

    env.service._resolve_command = not_installed
    events = await env.collect("hello")
    assert "not installed" in texts(events) and events[-1]["type"] == "final"
    assert "not installed" in (await env.messages())[-1].content


@pytest.mark.asyncio
async def test_unauthenticated_agent_is_reported_in_the_conversation(env):
    env.flags = ["--require-auth"]
    events = await env.collect("hello")
    assert texts(events) == SIGN_IN_NOTICE


@pytest.mark.asyncio
async def test_remote_workspace_is_refused_before_starting_an_agent(env):
    events = await env.collect("hello", workspace={"backend": "daytona", "locator": "sandbox-1", "metadata": {"root": "/w"}})
    assert "local workspace" in texts(events)
    assert not env.calls("initialize")


# Settings ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_is_sent_through_the_vendor_append_field(env):
    env.agent = AgentConfig(name="ag", graph_type="acp_agent", system_prompt="Be brief.", acp=AcpAgentSettings())
    await env.collect("hello")
    params = env.calls("session/new")[0]["params"]
    assert params["_meta"]["antigravity"]["systemPrompt"]["append"] == "Be brief."
    assert "Be brief." not in json.dumps(env.calls("session/prompt"))


@pytest.mark.asyncio
async def test_config_options_are_applied_once_per_session(env):
    env.agent = AgentConfig(name="ag", graph_type="acp_agent", acp=AcpAgentSettings(
        config_options={"model": "fake-lite", "thinking_level": "high"},
    ))
    await env.collect("one")
    await env.collect("two")
    applied = env.calls("session/set_config_option")
    assert [(call["params"]["configId"], call["params"]["value"]) for call in applied] == [("model", "fake-lite")]


@pytest.mark.asyncio
async def test_unavailable_config_option_is_reported_and_the_turn_continues(env):
    env.agent = AgentConfig(name="ag", graph_type="acp_agent", acp=AcpAgentSettings(config_options={"model": "retired"}))
    events = await env.collect("hello")
    assert "kept its current 'model'" in texts(events) and texts(events).endswith("hello")


@pytest.mark.asyncio
async def test_run_options_override_the_card_for_that_turn(env):
    env.agent = AgentConfig(name="ag", graph_type="acp_agent", acp=AcpAgentSettings(
        config_options={"model": "fake-lite", "thinking_level": "low"},
    ))
    await env.collect("one", acp_config_options={"thinking_level": "high"})
    applied = env.calls("session/set_config_option")
    assert [(call["params"]["configId"], call["params"]["value"]) for call in applied] == [("model", "fake-lite")]


@pytest.mark.asyncio
async def test_run_options_can_change_during_a_conversation(env):
    await env.collect("one", acp_config_options={"thinking_level": "low"})
    await env.collect("two", acp_config_options={"thinking_level": "low"})
    await env.collect("three", acp_config_options={"thinking_level": "high"})
    applied = env.calls("session/set_config_option")
    assert [call["params"]["value"] for call in applied] == ["low", "high"]
    assert len({call["params"]["sessionId"] for call in env.calls("session/prompt")}) == 1


@pytest.mark.asyncio
async def test_run_options_are_applied_again_after_the_agent_process_restarts(env):
    # The real agent resumes a session with its default settings, as the fake does.
    await env.collect("one", acp_config_options={"thinking_level": "low"})
    await env.service.restart_client("antigravity-acp")
    await env.collect("two", acp_config_options={"thinking_level": "low"})
    assert len(env.calls("session/resume")) == 1
    assert [call["params"]["value"] for call in env.calls("session/set_config_option")] == ["low", "low"]


@pytest.mark.asyncio
async def test_model_is_set_before_the_options_that_depend_on_it(env):
    await env.collect("one", acp_config_options={"thinking_level": "low", "model": "fake-lite"})
    applied = env.calls("session/set_config_option")
    assert [call["params"]["configId"] for call in applied] == ["model", "thinking_level"]


@pytest.mark.asyncio
async def test_mode_cannot_be_changed_because_it_would_bypass_approvals(env):
    env.agent = AgentConfig(name="ag", graph_type="acp_agent", acp=AcpAgentSettings(config_options={"mode": "yolo"}))
    first = await env.collect("one")
    second = await env.collect("two", acp_config_options={"mode": "yolo"})
    assert env.calls("session/set_config_option") == []
    assert "K41 controls tool approvals" in texts(first) and "K41 controls tool approvals" in texts(second)


def test_build_run_params_passes_acp_options_only_when_set():
    with_options = runner_module.build_run_params(
        platform="api", user_id="u", user_input="hi", thread_id=THREAD, acp_config_options={"model": "m"},
    )
    without = runner_module.build_run_params(platform="api", user_id="u", user_input="hi", thread_id=THREAD)
    assert with_options["acp_config_options"] == {"model": "m"} and "acp_config_options" not in without


def test_card_round_trips_acp_settings_and_drops_tool_settings():
    config = AgentConfig(
        name="ag", graph_type="acp_agent", tools=["read"], sub_agents=["x"], plan_approval_targets=["y"],
        acp=AcpAgentSettings(permission_mode="auto_allow", config_options={"model": "m"}),
    )
    assert config.tools == [] and config.sub_agents is None and config.plan_approval_targets == []
    parsed = parse_agent_markdown_content(serialize_agent_config(config), source_label="test")
    assert parsed.acp == config.acp and parsed.graph_type == "acp_agent"


def test_acp_settings_are_only_defaulted_for_acp_cards():
    assert AgentConfig(name="ag", graph_type="acp_agent").acp == AcpAgentSettings()
    assert AgentConfig(name="other", graph_type="react_agent").acp is None
