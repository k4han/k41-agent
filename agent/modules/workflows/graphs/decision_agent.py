"""Decision-guided agent graph: user input -> decision model -> LLM <> tool loop."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.modules.workflows.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.registry import (
    GraphRegistry,
)
from agent.modules.workflows.nodes.decision import decision_node
from agent.modules.workflows.nodes.llm import llm_node
from agent.modules.workflows.nodes.tool import tool_node
from agent.modules.workflows.nodes.trim import (
    make_prepare_context_node,
)
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.extensions import DecisionAgentState


def _should_continue(state: DecisionAgentState) -> str:
    """Continue the loop if the last model message contains tool calls."""
    messages = state.get("messages", [])
    if not messages:
        return END
    last = messages[-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tool"
    return END


def build_decision_agent_graph(
    checkpointer=None,
    *,
    graph_name: str = "decision_agent",
) -> None:
    """Build a decision-guided agent graph template.

    Workflow:
    START -> prepare_context -> decision -> llm -> (conditional)
                                              ^        |
                                              |        v
                                              +-- tool +
                                                       |
                                                       v
                                                      END
    """
    if checkpointer is None:
        try:
            checkpointer = get_checkpointer()
        except RuntimeError:
            checkpointer = None

    graph = StateGraph(DecisionAgentState, context_schema=WorkflowContext)
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("decision", decision_node)
    graph.add_node("llm", llm_node)
    graph.add_node("tool", tool_node)

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "decision")
    graph.add_edge("decision", "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")

    GraphRegistry.register(
        graph_name,
        graph.compile(checkpointer=checkpointer),
        description=(
            "decision-guided agent with proactive directory inspection and curated file context"
        ),
    )


__all__ = ["build_decision_agent_graph", "_should_continue"]
