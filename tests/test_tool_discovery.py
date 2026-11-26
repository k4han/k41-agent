"""Agent tool discovery through the card, registry and checkpointed tool node."""

import json
from dataclasses import replace
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

from agent.modules.agents.models import AgentConfig
from agent.modules.agents.parser import (
    parse_agent_markdown_content,
    serialize_agent_config,
)
from agent.modules.decisions import ChoiceAnswer
from agent.modules.tools import (
    DISCOVERY_TOOL_NAMES,
    RiskLevel,
    ToolCategory,
    ToolResolver,
    ToolSource,
    discovery_catalog,
)
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.modules.workflows.nodes.llm import llm_node
from agent.modules.workflows.nodes.tool import tool_node
from agent.modules.workflows.run_config import WorkflowContext, make_context


@pytest.fixture
def card(isolated_container, monkeypatch):
    card = AgentConfig(
        name="worker",
        graph_type="react_agent",
        tools=[*DISCOVERY_TOOL_NAMES, "web_search"],
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: SimpleNamespace(get_agent=lambda name: card),
    )
    monkeypatch.setattr(
        "agent.modules.mcp.list_agent_mcp_server_names", lambda _: ["installed"]
    )
    isolated_container._mcp_loaded = True
    isolated_container._decision_service = SimpleNamespace(is_configured=False)
    return card


@pytest.fixture
def graph():
    builder = StateGraph(MessagesState, context_schema=WorkflowContext)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile(checkpointer=InMemorySaver())


def call(name, identifier, **arguments):
    return {"name": name, "id": identifier, "args": arguments}


def dispatch(name, identifier, **arguments):
    return call("execute_tool", identifier, tool_name=name, arguments=arguments)


async def invoke(graph, calls, tmp_path, *, allowed=None, mode="full_access"):
    config = {"configurable": {"thread_id": calls[0]["id"], "approval_supported": True}}
    context = make_context(
        working_dir=str(tmp_path),
        agent_name="worker",
        allowed_tool_names=allowed,
        permission_mode=mode,
    )
    return await graph.ainvoke(
        {"messages": [AIMessage(content="", tool_calls=calls)]}, config, context=context
    )


@pytest.mark.asyncio
async def test_search_and_dispatch_without_binding_targets(
    card, graph, tmp_path, monkeypatch
):
    expected = {"web_search", *DISCOVERY_TOOL_NAMES}
    assert {
        t.name for t in await ToolResolver().aresolve_for_agent(card.name)
    } == expected
    result = await invoke(graph, [call("search_tools", "web", query="web")], tmp_path)
    definitions = {
        entry["name"]: entry for entry in json.loads(result["messages"][-1].content)
    }
    assert "web_fetch" in definitions and "web_search" not in definitions
    assert definitions["web_fetch"]["description"]
    assert "url" in definitions["web_fetch"]["parameters"]["properties"]
    assert "runtime" not in definitions["web_fetch"]["parameters"]["properties"]
    display = result["messages"][-1].artifact["display_content"]
    assert display.splitlines() == list(definitions)
    card.tool_configs = {"web_fetch": {"provider": "local"}}
    monkeypatch.setattr(
        import_module("agent.modules.tools.builtin.web.web_fetch"),
        "_local_fetch",
        lambda url, **kwargs: f"Fetched {url}",
    )
    result = await invoke(
        graph, [dispatch("web_fetch", "fetch", url="https://example.test")], tmp_path
    )
    assert result["messages"][-1].content == "Fetched https://example.test"
    result = await invoke(graph, [dispatch("get_current_time", "known")], tmp_path)
    receipt = result["messages"][-1]
    assert (
        receipt.status == "success"
        and receipt.tool_call_id == "known"
        and receipt.name == "get_current_time"
    )
    assert (
        normalize_messages_for_chat_model(result["messages"])[-1].name == "execute_tool"
    )
    assert {
        t.name for t in await ToolResolver().aresolve_for_agent(card.name)
    } == expected


@pytest.mark.asyncio
async def test_model_binding_after_discovery(card, graph, tmp_path, monkeypatch):
    bound = []
    messages = []

    class Model:
        def bind_tools(self, tools, **kwargs):
            bound.append({t.name: convert_to_openai_tool(t)["function"] for t in tools})
            return self

        async def ainvoke(self, history, **kwargs):
            messages.append(history)
            return AIMessage(content="done")

    resolved = SimpleNamespace(
        model=Model(),
        provider_name="default",
        provider_type="openai_compatible",
        model_name="test",
    )
    monkeypatch.setattr(
        "agent.modules.workflows.nodes.llm.get_resolved_chat_model",
        lambda **kwargs: resolved,
    )
    runtime = SimpleNamespace(
        context=make_context(working_dir=str(tmp_path), agent_name="worker")
    )
    config = {"configurable": {"thread_id": "binding"}}
    history = [HumanMessage(content="Find a tool to fetch a page.")]
    await llm_node({"messages": history}, config, runtime)
    found = await invoke(
        graph, [call("search_tools", "schemas", query="web_fetch")], tmp_path
    )
    await llm_node({"messages": [*history, *found["messages"]]}, config, runtime)
    assert bound[0] == bound[1]
    assert set(bound[0]) == {*DISCOVERY_TOOL_NAMES, "web_search", "read"}
    assert set(bound[0]["execute_tool"]["parameters"]["properties"]) == {
        "tool_name",
        "arguments",
    }
    assert set(bound[0]["search_tools"]["parameters"]["properties"]) == {"query"}
    assert json.loads(messages[-1][-1].content)[0]["name"] == "web_fetch"


@pytest.mark.asyncio
async def test_group_decisions_fallback_and_large_result(
    card, isolated_container, graph, tmp_path
):
    registry = isolated_container.tool_registry_service
    original = registry.registry.get("get_current_time")
    long_description = "Clock documentation. " * 3000
    registry.load_descriptors(
        [
            replace(
                original,
                id="clock.extra",
                name="clock_details",
                tool=original.tool.model_copy(
                    update={"name": "clock_details", "description": long_description}
                ),
                description=long_description,
            )
        ]
    )
    service = SimpleNamespace(
        is_configured=True,
        evaluate=AsyncMock(
            return_value=ChoiceAnswer(
                choice="clock_details", confidence=1, probabilities={"clock_details": 1}
            )
        ),
    )
    isolated_container._decision_service = service
    # Use escapes so the test exercises a Vietnamese query with an ASCII source file.
    query = "xem gi\u1edd hi\u1ec7n t\u1ea1i"
    result = await invoke(graph, [call("search_tools", "large", query=query)], tmp_path)
    message = result["messages"][-1]
    assert len(message.content.encode()) > 50 * 1024
    assert [d["name"] for d in json.loads(message.content)] == ["clock_details"]
    question = service.evaluate.await_args.args[0]
    assert (
        "utility" not in question.criteria
        and {"clock_details", "get_current_time", "web"} <= question.criteria.keys()
    )
    assert query in service.evaluate.await_args.kwargs["state"]
    service.evaluate.side_effect = RuntimeError("provider offline")
    result = await invoke(
        graph, [call("search_tools", "local", query="clock documentation")], tmp_path
    )
    assert [d["name"] for d in json.loads(result["messages"][-1].content)] == [
        "clock_details"
    ]
    result = await invoke(
        graph, [call("search_tools", "none", query="zxqww")], tmp_path
    )
    assert "No matching" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_mcp_run_and_dispatch_boundaries(
    card, isolated_container, graph, tmp_path
):
    registry = isolated_container.tool_registry_service
    original = registry.registry.get("get_current_time")
    for server in ["installed", "disabled"]:
        for operation in ["list", "get"]:
            name = f"mcp__{server}__{operation}"
            registry.load_descriptors(
                [
                    replace(
                        original,
                        id=name,
                        source=ToolSource.MCP,
                        name=name,
                        tool=original.tool.model_copy(update={"name": name}),
                    )
                ]
            )
    result = await invoke(
        graph, [call("search_tools", "mcp", query="mcp:installed")], tmp_path
    )
    assert [d["name"] for d in json.loads(result["messages"][-1].content)] == [
        "mcp__installed__get",
        "mcp__installed__list",
    ]
    for name in ["mcp__disabled__get", "execute_tool", "search_tools", "delegate_task"]:
        result = await invoke(graph, [dispatch(name, name)], tmp_path)
        assert result["messages"][-1].status == "error"
    card.tools = [*card.tools, "get_current_time"]
    allowed = [*DISCOVERY_TOOL_NAMES, "web_fetch", "get_current_time"]
    result = await invoke(
        graph,
        [call("search_tools", "restricted", query="web")],
        tmp_path,
        allowed=allowed,
    )
    assert "No matching" in result["messages"][-1].content
    result = await invoke(
        graph, [dispatch("web_fetch", "denied", url="https://example.test")], tmp_path, allowed=allowed
    )
    assert result["messages"][-1].status == "error"
    result = await invoke(graph, [dispatch("get_current_time", "allowed")], tmp_path, allowed=allowed)
    assert result["messages"][-1].status == "success"
    card.graph_type = "acp_agent"
    catalog = discovery_catalog(await ToolResolver().aresolve_for_agent(card.name))
    assert "ask_user" not in catalog.deferred_for_run(str(tmp_path), {})


@pytest.mark.asyncio
async def test_interactive_control_tools_require_explicit_selection(card, graph, tmp_path):
    tools = await ToolResolver().aresolve_for_agent(card.name)
    catalog = discovery_catalog(tools)
    assert catalog.deferred_for_run(str(tmp_path), {}).keys().isdisjoint({
        "ask_user", "browser_handoff", "plan_mode_respond", "request_plan_review", "write_todos",
    })
    for control in (call, dispatch):
        result = await invoke(
            graph, [control("request_plan_review", control.__name__, plan="Review the change.")], tmp_path,
        )
        assert not result.get("__interrupt__")
        assert result["messages"][-1].status == "error"


@pytest.mark.asyncio
async def test_deferred_computer_tools_require_desktop_access(card):
    catalog = discovery_catalog(await ToolResolver().aresolve_for_agent(card.name))
    catalog = replace(catalog, auxiliary_machines=False)
    assert "computer_screenshot" in catalog.descriptors
    assert "computer_screenshot" not in catalog.deferred_for_run(None, {})
    assert "computer_screenshot" in catalog.deferred_for_run(
        {"backend": "instabox", "locator": "desktop"}, {},
    )


@pytest.mark.asyncio
async def test_direct_retained_read_and_dispatched_full_read_in_same_batch(
    card, graph, tmp_path
):
    (tmp_path / "sample.txt").write_text("workspace contents", encoding="utf-8")
    result = await invoke(
        graph,
        [
            call("read", "limited", path="sample.txt"),
            dispatch("read", "full", path="sample.txt"),
        ],
        tmp_path,
    )
    limited, full = result["messages"][-2:]
    assert limited.status == "error" and "retained" in limited.content
    assert full.status == "success" and "workspace contents" in full.content


@pytest.mark.asyncio
async def test_validation_mixed_batch_and_real_target_permissions(
    card, isolated_container, graph, tmp_path
):
    effects = []

    @tool(response_format="content_and_artifact")
    def change_record(value: int) -> tuple[str, dict]:
        """Update a record."""
        effects.append(value)
        return str(value), {"value": value}

    registry = isolated_container.tool_registry_service
    registry.load_descriptors(
        [
            replace(
                registry.registry.get("get_current_time"),
                id="record.change",
                category=ToolCategory.UTILITY,
                name=change_record.name,
                tool=change_record,
                risk=RiskLevel.MUTATING,
            )
        ]
    )
    invalid = await invoke(
        graph, [dispatch("change_record", "invalid", value="bad")], tmp_path
    )
    assert invalid["messages"][-1].status == "error" and not effects
    mixed = await invoke(
        graph,
        [
            dispatch("change_record", "valid", value=1),
            call("change_record", "raw", value=2),
        ],
        tmp_path,
    )
    assert all(m.status == "error" for m in mixed["messages"][-2:]) and not effects
    calls = [dispatch("change_record", "approve", value=7)]
    first = await invoke(graph, calls, tmp_path, mode="ask")
    request = first["__interrupt__"][0].value
    assert "change_record" in str(request) and not effects
    result = await graph.ainvoke(
        Command(
            resume={
                "action": "permission",
                "request_id": request["request_id"],
                "decision": "allow_once",
            }
        ),
        {"configurable": {"thread_id": "approve", "approval_supported": True}},
        context=make_context(
            working_dir=str(tmp_path), agent_name="worker", permission_mode="ask"
        ),
    )
    receipt = result["messages"][-1]
    assert (
        effects == [7]
        and receipt.tool_call_id == "approve"
        and receipt.artifact == {"value": 7}
    )


def test_card_round_trip_and_partial_pair(card):
    from agent.delivery.http.dashboard.routes.agents import AgentCardBody
    from agent.delivery.http.dashboard.routes.helpers.agents import (
        agent_config_from_body,
    )

    config = {"web_fetch": {"provider": "local"}}
    body = AgentCardBody(name="worker", tools=card.tools, tool_configs=config)
    restored = parse_agent_markdown_content(
        serialize_agent_config(agent_config_from_body(body))
    )
    assert restored.tool_configs == config and restored.tools == card.tools
    for names in ([], ["search_tools"], ["execute_tool"]):
        card.tools = ["web_search", *names]
        assert {t.name for t in ToolResolver().resolve_for_agent(card.name)} == {
            "web_search"
        }
        assert not agent_config_from_body(
            body.model_copy(update={"tools": card.tools})
        ).tool_configs


@pytest.mark.asyncio
@pytest.mark.parametrize("with_siblings", [False, True])
@pytest.mark.parametrize("pending_config", ["runner", "missing", "empty"])
async def test_dispatched_plan_resumes_after_handoff(
    card, graph, tmp_path, monkeypatch, isolated_container, with_siblings, pending_config,
):
    from agent.modules.agent_runtime.runner import _allow_pending_control_resume

    card.tools.append("request_plan_review")
    recipient = AgentConfig(
        name="implementer", graph_type="react_agent", tools=[], permission_mode="ask"
    )
    cards = {card.name: card, recipient.name: recipient}
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: SimpleNamespace(get_agent=cards.get),
    )
    effects = []

    @tool
    def update_record(value: int) -> str:
        """Record an update."""
        effects.append(value)
        return str(value)

    registry = isolated_container.tool_registry_service
    registry.load_descriptors([
        replace(
            registry.registry.get("get_current_time"),
            id="record.update",
            name=update_record.name,
            tool=update_record,
            risk=RiskLevel.MUTATING,
        )
    ])
    calls = [dispatch("request_plan_review", "plan", plan="Implement the change.")]
    if with_siblings:
        calls = [
            dispatch("update_record", "before", value=1),
            *calls,
            dispatch("update_record", "after", value=2),
        ]
    config = {
        "configurable": {"thread_id": calls[0]["id"], "approval_supported": True}
    }
    first = await invoke(graph, calls, tmp_path)
    assert first["__interrupt__"][0].value["type"] == "plan_review"
    assert effects == ([1] if with_siblings else [])
    if pending_config == "runner":
        _allow_pending_control_resume(config, await graph.aget_state(config))
    elif pending_config == "empty":
        config["configurable"]["pending_control_tool_call_ids"] = []
    result = await graph.ainvoke(
        Command(resume={"action": "approve", "target_agent": recipient.name}),
        config,
        context=make_context(working_dir=str(tmp_path), agent_name=recipient.name),
    )
    receipts = {message.tool_call_id: message for message in result["messages"][1:]}
    message = receipts["plan"]
    assert message.status == "success"
    assert "Target agent: implementer" in message.content
    if with_siblings:
        assert effects == [1]
        assert receipts["before"].status == "success"
        assert receipts["after"].status == "error"
        assert "switched agents" in receipts["after"].content


@pytest.mark.asyncio
async def test_direct_plan_resumes_on_agent_with_discovery(
    card, graph, tmp_path, monkeypatch
):
    from agent.modules.agent_runtime.runner import _allow_pending_control_resume

    planner = AgentConfig(
        name="planner", graph_type="react_agent", tools=["request_plan_review"]
    )
    cards = {card.name: card, planner.name: planner}
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: SimpleNamespace(get_agent=cards.get),
    )
    config = {"configurable": {"thread_id": "direct-plan", "approval_supported": True}}
    first = await graph.ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        call(
                            "request_plan_review",
                            "direct",
                            plan="Implement the change.",
                        )
                    ],
                )
            ]
        },
        config,
        context=make_context(working_dir=str(tmp_path), agent_name=planner.name),
    )
    assert first["__interrupt__"]
    _allow_pending_control_resume(config, await graph.aget_state(config))
    assert config["configurable"]["pending_control_tool_call_ids"] == ["direct"]
    result = await graph.ainvoke(
        Command(resume={"action": "approve", "target_agent": card.name}),
        config,
        context=make_context(working_dir=str(tmp_path), agent_name=card.name),
    )
    assert "Target agent: worker" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_dispatched_plan_resumes_after_discovery_is_disabled(card, graph, tmp_path):
    card.tools.append("request_plan_review")
    calls = [
        dispatch("get_current_time", "before"),
        dispatch("request_plan_review", "plan", plan="Implement the change."),
        dispatch("get_current_time", "after"),
    ]
    first = await invoke(graph, calls, tmp_path)
    pending = first["__interrupt__"][0]
    assert pending.value["type"] == "plan_review"
    card.tools = []
    result = await graph.ainvoke(
        Command(resume={pending.id: {"action": "approve", "target_agent": card.name}}),
        {"configurable": {"thread_id": "before", "approval_supported": True}},
        context=make_context(working_dir=str(tmp_path), agent_name=card.name),
    )
    before, review, after = result["messages"][-3:]
    assert before.name == "get_current_time" and before.status == "success"
    assert review.name == "request_plan_review" and review.status == "success"
    assert "tool access changed" in after.content and after.status == "error"


@pytest.mark.asyncio
async def test_direct_question_resumes_from_checkpoint_without_runner_config(card, graph, tmp_path):
    card.tools.append("ask_user")
    question = call("ask_user", "question", questions=[{
        "id": "choice", "question": "Pick one", "options": [{"id": "a", "label": "A"}],
    }])
    first = await invoke(graph, [question], tmp_path)
    pending = first["__interrupt__"][0]
    card.tools.remove("ask_user")
    result = await graph.ainvoke(
        Command(resume={pending.id: {"action": "answer", "answers": [
            {"question_id": "choice", "selected_option_ids": ["a"]},
        ]}}),
        {"configurable": {"thread_id": "question", "approval_supported": True}},
        context=make_context(working_dir=str(tmp_path), agent_name=card.name),
    )
    receipt = result["messages"][-1]
    assert receipt.name == "ask_user" and receipt.status == "success"
    assert json.loads(receipt.content)["answers"][0]["selected_option_ids"] == ["a"]
