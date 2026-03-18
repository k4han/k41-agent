# agent/graphs/router.py
# Router graph: receives user input, auto-classifies to appropriate workflow

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END

from agent.persistence import get_checkpointer
from agent.registry import GraphRegistry
from agent.state.base import BaseState
from agent.providers.llm import get_llm


ROUTER_SYSTEM = """You are a router agent. Your task is to classify user requests 
into the correct workflow. Respond with exactly one of the following values (no explanation):
- chat_agent      : general Q&A, knowledge questions
- coding_agent    : writing code, reading/writing files, running bash commands
- research_chain  : research, information synthesis, analysis

User request: {user_input}"""


async def _router_node(state: BaseState, config: RunnableConfig) -> dict:
    """Classify request and route to appropriate graph."""
    user_input = state["messages"][-1].content
    llm        = get_llm()

    response = await llm.ainvoke([
        SystemMessage(content=ROUTER_SYSTEM.format(user_input=user_input))
    ])

    workflow = response.content.strip().lower()
    valid    = {"chat_agent", "coding_agent", "research_chain"}
    if workflow not in valid:
        workflow = "chat_agent"  # fallback

    # Get registered graph and continue
    target_graph = GraphRegistry.get(workflow)
    result       = await target_graph.ainvoke(
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
