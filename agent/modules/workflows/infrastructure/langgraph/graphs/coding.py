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
from agent.modules.workflows.infrastructure.langgraph.state.extensions import CodingState
from agent.modules.workflows.infrastructure.langgraph.tools.common import (
    list_files,
    read_file,
    run_bash,
    write_file,
)

SYSTEM_PROMPTS = {
    "default": "Bạn là coding assistant.\nWorking directory: {working_dir}",
    "backend": (
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
    graph.add_node("llm", make_llm_node(tools, system_prompts=SYSTEM_PROMPTS))
    graph.add_node("tool", make_tool_node(tools))

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")

    GraphRegistry.register(
        "coding_agent",
        graph.compile(checkpointer=get_checkpointer()),
    )
