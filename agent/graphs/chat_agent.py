# agent/graphs/chat_agent.py

from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, START, END

from agent.registry import GraphRegistry
from agent.state.base import BaseState
from agent.nodes.llm_node import make_llm_node
from agent.nodes.tool_node import make_tool_node
from agent.tools.chat import get_current_time, echo


def _should_continue(state: BaseState) -> str:
    """Nếu LLM gọi tool thì tiếp tục, không thì kết thúc."""
    last: AIMessage = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tool"
    return END


def build_chat_graph() -> None:
    tools = [get_current_time, echo]

    graph = StateGraph(BaseState)
    graph.add_node("llm",  make_llm_node(tools))
    graph.add_node("tool", make_tool_node(tools))

    graph.add_edge(START, "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")

    GraphRegistry.register("chat_agent", graph.compile())
