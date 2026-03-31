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
from agent.modules.workflows.infrastructure.langgraph.run_config import (
    WorkflowContext,
)
from agent.modules.workflows.infrastructure.langgraph.state.base import BaseState
from agent.modules.workflows.infrastructure.langgraph.tools.chat import (
    echo,
    get_current_time,
)
from agent.modules.workflows.infrastructure.langgraph.tools.common import (
    list_files,
    read_file,
    run_bash,
    write_file,
)
from agent.modules.workflows.infrastructure.langgraph.tools.skills import (
    skill,
)

SYSTEM_PROMPTS = {
    "default": "You are a helpful AI assistant.\nWorking directory: {working_dir}",
    "backend": (
        "You are a Python/backend engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Focus on Pythonic implementations, type hints, and maintainable code."
    ),
    "frontend": (
        "You are a React/TypeScript frontend engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Prefer functional components, hooks, and modern frontend best practices."
    ),
    "devops": (
        "You are a DevOps engineer assistant.\n"
        "Working directory: {working_dir}\n"
        "Help with Docker, CI/CD, shell automation, and deployment operations."
    ),
}


def _should_continue(state: BaseState) -> str:
    """Continue the loop if the last model message contains tool calls."""
    last: AIMessage = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tool"
    return END


def build_react_graph() -> None:
    tools = [get_current_time, echo, skill, read_file, write_file, run_bash, list_files]

    graph = StateGraph(BaseState, context_schema=WorkflowContext)
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("llm", make_llm_node(tools, system_prompts=SYSTEM_PROMPTS))
    graph.add_node("tool", make_tool_node(tools))

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tool": "tool", END: END})
    graph.add_edge("tool", "llm")

    GraphRegistry.register(
        "react_agent",
        graph.compile(checkpointer=get_checkpointer()),
    )
