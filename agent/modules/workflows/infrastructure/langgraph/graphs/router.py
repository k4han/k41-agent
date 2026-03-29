from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent.modules.workflows.infrastructure.langgraph.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.infrastructure.langgraph.compiled_registry import (
    GraphRegistry,
)
from agent.modules.workflows.infrastructure.langgraph.state.base import BaseState
from agent.modules.providers.public import get_chat_model

ROUTER_SYSTEM = """You are a router agent. Your task is to classify user requests
into the correct workflow. Respond with exactly one of the following values (no explanation):
- chat_agent      : general Q&A, knowledge questions
- coding_agent    : writing code, reading/writing files, running bash commands
- research_chain  : research, information synthesis, analysis

User request: {user_input}"""


async def _router_node(state: BaseState, config: RunnableConfig) -> dict:
    """Classify request and route to appropriate graph."""
    user_input = state["messages"][-1].content
    llm = get_chat_model()

    response = await llm.ainvoke([
        SystemMessage(content=ROUTER_SYSTEM.format(user_input=user_input))
    ])

    workflow = response.content.strip().lower()
    valid = {"chat_agent", "coding_agent", "research_chain"}
    if workflow not in valid:
        workflow = "chat_agent"

    target_graph = GraphRegistry.get(workflow)
    result = await target_graph.ainvoke(
        {"messages": state["messages"]},
        config=config,
    )
    return {"messages": result["messages"]}


def build_router_graph() -> None:
    graph = StateGraph(BaseState)
    graph.add_node("router", _router_node)
    graph.add_edge(START, "router")
    graph.add_edge("router", END)

    GraphRegistry.register(
        "router",
        graph.compile(checkpointer=get_checkpointer()),
    )
