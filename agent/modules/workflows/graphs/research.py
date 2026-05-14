from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent.modules.workflows.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.registry import (
    GraphRegistry,
)
from agent.modules.workflows.nodes.trim import (
    make_prepare_context_node,
)
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.extensions import (
    ResearchState,
)
from agent.modules.providers import get_chat_model
from agent.shared.infrastructure.parsing import extract_final_text_content


def _resolve_runtime_model(runtime):
    from agent.modules.agents import get_catalog_service

    ctx = runtime.context
    catalog = get_catalog_service()
    agent_config = catalog.get_agent(ctx.get_agent_name())
    if agent_config is None:
        raise RuntimeError(f"Agent '{ctx.get_agent_name()}' not found in catalog.")
    provider = ctx.get_provider() or agent_config.provider
    model = ctx.get_model() or agent_config.model or None
    return get_chat_model(provider_name=provider, model=model)


async def _research_node(state: ResearchState, config: RunnableConfig, runtime):
    """Collect research directions for the request."""
    llm = _resolve_runtime_model(runtime)
    system = SystemMessage(content=(
        "You are a research assistant. "
        "Analyze the request and list the information sources to investigate."
    ))
    response = await llm.ainvoke([system, *state["messages"]])
    return {"messages": [response]}


async def _summarize_node(state: ResearchState, config: RunnableConfig, runtime):
    """Summarize the collected research context."""
    llm = _resolve_runtime_model(runtime)
    system = SystemMessage(content=(
        "Based on the collected information, write a concise report with "
        "clear sections: Summary, Key Points, and Conclusion."
    ))
    response = await llm.ainvoke([system, *state["messages"]])
    return {
        "messages": [response],
        "summary": extract_final_text_content(response.content),
    }


def build_research_graph() -> None:
    graph = StateGraph(ResearchState, context_schema=WorkflowContext)
    graph.add_node("prepare_context", make_prepare_context_node())
    graph.add_node("research", _research_node)
    graph.add_node("summarize", _summarize_node)

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "research")
    graph.add_edge("research", "summarize")
    graph.add_edge("summarize", END)

    GraphRegistry.register(
        "research_chain",
        graph.compile(checkpointer=get_checkpointer()),
        description="research, information synthesis, and multi-step analysis",
    )
