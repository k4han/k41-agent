"""React-agent graph template — single shared graph, config resolved at runtime."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from agent.modules.workflows.infrastructure.langgraph.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.infrastructure.langgraph.compiled_registry import (
    GraphRegistry,
)
from agent.modules.workflows.infrastructure.langgraph.nodes.llm import llm_node
from agent.modules.workflows.infrastructure.langgraph.nodes.tool import make_tool_node
from agent.modules.workflows.infrastructure.langgraph.nodes.trim import (
    make_prepare_context_node,
)
from agent.modules.workflows.infrastructure.langgraph.run_config import WorkflowContext
from agent.modules.workflows.infrastructure.langgraph.state.base import BaseState
from agent.modules.workflows.infrastructure.langgraph.tools.registry import (
    get_tool_by_name,
    get_default_tools,
)


def _should_continue(state: BaseState) -> str:
    """Continue the loop if the last model message contains tool calls."""
    last: AIMessage = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tool"
    return END


def build_react_graph(
    checkpointer=None,
    *,
    graph_name: str = "react_agent",
) -> None:
    """Build a SINGLE react_agent graph template.

    All agent-specific config (model, system prompt, tools) is resolved at
    runtime by llm_node via WorkflowContext.agent_name.
    """
    if checkpointer is None:
        checkpointer = get_checkpointer()

    tools = get_default_tools()

    graph = StateGraph(BaseState, context_schema=WorkflowContext)
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("llm", llm_node)
    graph.add_node("tool", make_tool_node(tools))

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")

    GraphRegistry.register(
        graph_name,
        graph.compile(checkpointer=checkpointer),
    )


__all__ = ["build_react_graph", "get_tool_by_name", "get_default_tools"]
