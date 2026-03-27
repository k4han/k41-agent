# agent/graphs/coding_agent.py

from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, START, END

from agent.persistence import get_checkpointer
from agent.registry import GraphRegistry
from agent.state.extensions import CodingState
from agent.nodes.llm_node import make_llm_node
from agent.nodes.tool_node import make_tool_node
from agent.nodes.trim_node import make_prepare_context_node
from agent.tools.common import read_file, write_file, run_bash, list_files

SYSTEM_PROMPTS = {
    "default":  "Bạn là coding assistant.\nWorking directory: {working_dir}",
    "backend":  (
        "Bạn là Python/backend engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Chỉ thao tác file trong thư mục này. Ưu tiên viết code pythonic, có type hints."
    ),
    "frontend": (
        "Bạn là React/TypeScript frontend engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Ưu tiên functional components, hooks, và best practices."
    ),
    "devops": (
        "Bạn là DevOps engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Hỗ trợ Docker, CI/CD, shell scripting."
    ),
}


def _should_continue(state: CodingState) -> str:
    last: AIMessage = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tool"
    return END


def build_coding_graph() -> None:
    tools = [read_file, write_file, run_bash, list_files]

    graph = StateGraph(CodingState)
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("llm",  make_llm_node(tools, system_prompts=SYSTEM_PROMPTS))
    graph.add_node("tool", make_tool_node(tools))

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")

    GraphRegistry.register(
        "coding_agent",
        graph.compile(checkpointer=get_checkpointer()),
    )
