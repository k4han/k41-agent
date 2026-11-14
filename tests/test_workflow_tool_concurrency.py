"""Independent reads overlap without breaking barriers or durable replay."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.modules.tools.permissions.risk import RiskLevel
from agent.modules.workflows.nodes import tool as tool_module
from agent.modules.workflows.run_config import WorkflowContext, make_context
from agent.modules.workflows.state.base import BaseState


def compile_tools(monkeypatch, tools):
    async def resolve(self, agent_name, **kwargs):
        return tools

    monkeypatch.setattr(tool_module.ToolResolver, "aresolve_for_agent", resolve)
    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("tools", tool_module.tool_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile(checkpointer=InMemorySaver())


def tool_input(calls):
    return {"messages": [HumanMessage(content="run tools"), AIMessage(content="", tool_calls=calls)]}


def call(name, identifier, **args):
    return {"name": name, "id": identifier, "args": args}


@pytest.mark.asyncio
async def test_reads_overlap_and_unknown_tools_form_ordering_barriers(monkeypatch, isolated_container, tmp_path):
    started = []
    finished = []
    overlap = asyncio.Event()

    @tool
    async def read_probe(index: int) -> str:
        """Read a test resource."""
        started.append(index)
        if index < 2:
            if len(started) == 2:
                overlap.set()
            await overlap.wait()
        else:
            assert finished == [0, 1, "mutation"] or finished == [1, 0, "mutation"]
        finished.append(index)
        return str(index)

    @tool
    async def mutate_probe() -> str:
        """Apply a test mutation."""
        assert sorted(finished) == [0, 1]
        finished.append("mutation")
        return "mutation"

    read_probe.metadata = {"parallel_safe": True, "permission_risk": RiskLevel.SAFE}
    mutate_probe.metadata = {"permission_risk": RiskLevel.SAFE}
    graph = compile_tools(monkeypatch, [read_probe, mutate_probe])
    calls = [call("read_probe", "r0", index=0), call("read_probe", "r1", index=1),
             call("mutate_probe", "w"), call("read_probe", "r2", index=2)]
    async with asyncio.timeout(5):
        result = await graph.ainvoke(tool_input(calls), {"configurable": {"thread_id": "barriers"}},
                                    context=make_context(working_dir=str(tmp_path)))
    messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert [message.tool_call_id for message in messages] == ["r0", "r1", "w", "r2"]
    assert [message.content for message in messages] == ["0", "1", "mutation", "2"]
    assert all(message.status == "success" for message in messages)


@pytest.mark.asyncio
async def test_parallel_reads_keep_completed_results_when_control_tool_resumes(monkeypatch, isolated_container, tmp_path):
    executed = []
    overlap = asyncio.Event()

    @tool
    async def read_probe(index: int) -> str:
        """Read once, including across graph resumption."""
        executed.append(index)
        if len(executed) == 2:
            overlap.set()
        await overlap.wait()
        return str(index)

    @tool
    async def control_probe() -> str:
        """Pause after reads and return the resume value."""
        return interrupt({"type": "probe"})

    read_probe.metadata = {"parallel_safe": True, "permission_risk": RiskLevel.SAFE}
    control_probe.metadata = {"permission_risk": RiskLevel.SAFE}
    graph = compile_tools(monkeypatch, [read_probe, control_probe])
    config = {"configurable": {"thread_id": "durable-parallel"}}
    context = make_context(working_dir=str(tmp_path))
    async with asyncio.timeout(5):
        first = await graph.ainvoke(tool_input([
            call("read_probe", "r0", index=0), call("read_probe", "r1", index=1),
            call("control_probe", "control"),
        ]), config, context=context)
        assert first["__interrupt__"]
        result = await graph.ainvoke(Command(resume="approved"), config, context=context)
    assert sorted(executed) == [0, 1]
    messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert [message.content for message in messages] == ["0", "1", "approved"]


@pytest.mark.asyncio
async def test_parallel_read_concurrency_is_bounded(monkeypatch, isolated_container, tmp_path):
    monkeypatch.setattr(tool_module, "MAX_PARALLEL_TOOL_CALLS", 2)
    active = peak = 0
    reached_limit = asyncio.Event()

    @tool
    async def read_probe(index: int) -> str:
        """Track concurrent reads."""
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            reached_limit.set()
        await reached_limit.wait()
        await asyncio.sleep(0)
        active -= 1
        return str(index)

    read_probe.metadata = {"parallel_safe": True, "permission_risk": RiskLevel.SAFE}
    graph = compile_tools(monkeypatch, [read_probe])
    async with asyncio.timeout(5):
        result = await graph.ainvoke(tool_input([call("read_probe", f"r{i}", index=i) for i in range(6)]),
                                    {"configurable": {"thread_id": "bounded"}},
                                    context=make_context(working_dir=str(tmp_path)))
    assert peak == 2
    assert len([message for message in result["messages"] if isinstance(message, ToolMessage)]) == 6


def test_only_known_independent_builtin_tools_opt_in():
    from agent.modules.tools.builtin.coding_tools import bash, read, read_process_output, targeted_tool, write
    from agent.modules.tools.builtin.web.web_fetch import _build_web_fetch_tool
    from agent.modules.tools.builtin.web.web_search import _build_web_search_tool

    for candidate in [read, targeted_tool("grep"), _build_web_fetch_tool({}), _build_web_search_tool({})]:
        assert candidate.metadata["parallel_safe"] is True
    for candidate in [bash, write, read_process_output]:
        assert candidate.metadata["parallel_safe"] is False
