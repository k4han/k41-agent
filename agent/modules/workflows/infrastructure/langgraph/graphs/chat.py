from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from agent.modules.workflows.infrastructure.langgraph.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.infrastructure.langgraph.compiled_registry import (
    GraphRegistry,
)
from agent.modules.workflows.infrastructure.langgraph.nodes.llm import make_llm_node
from agent.modules.workflows.infrastructure.langgraph.nodes.tool import make_tool_node
from agent.modules.workflows.infrastructure.langgraph.nodes.trim import (
    make_prepare_context_node,
)
from agent.modules.workflows.infrastructure.langgraph.state.base import BaseState
from agent.modules.workflows.infrastructure.langgraph.tools.chat import (
    echo,
    get_current_time,
)
from agent.modules.workflows.infrastructure.langgraph.tools.skills import (
    skill,
)
from agent.modules.workflows.infrastructure.langgraph.tools.common import (
    read_file,
    write_file,
    run_bash,
    list_files,
)

def _should_continue(state: BaseState) -> str:
    """Nếu LLM gọi tool thì tiếp tục, không thì kết thúc."""
    last: AIMessage = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tool"
    return END


def build_chat_graph() -> None:
    tools = [get_current_time, echo, skill, read_file, write_file, run_bash, list_files]

    graph = StateGraph(BaseState)
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("llm", make_llm_node(tools))
    graph.add_node("tool", make_tool_node(tools))

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")

    GraphRegistry.register(
        "chat_agent",
        graph.compile(checkpointer=get_checkpointer()),
    )
