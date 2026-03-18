# agent/graphs/research_chain.py

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END

from agent.persistence import get_checkpointer
from agent.registry import GraphRegistry
from agent.state.extensions import ResearchState
from agent.providers.llm import get_llm


async def _research_node(state: ResearchState, config: RunnableConfig):
    """Bước 1: Thu thập thông tin theo yêu cầu."""
    cfg          = config.get("configurable", {})
    service_type = cfg.get("service_type", "default")

    llm = get_llm()
    system = SystemMessage(content=(
        "Bạn là research assistant. "
        "Hãy phân tích yêu cầu và liệt kê các nguồn thông tin cần tìm hiểu."
    ))
    response = await llm.ainvoke([system, *state["messages"]])
    return {"messages": [response]}


async def _summarize_node(state: ResearchState, config: RunnableConfig):
    """Bước 2: Tổng hợp kết quả nghiên cứu."""
    llm = get_llm()
    system = SystemMessage(content=(
        "Dựa trên thông tin đã thu thập, hãy tổng hợp thành báo cáo ngắn gọn, "
        "có cấu trúc rõ ràng với: Tóm tắt, Điểm chính, Kết luận."
    ))
    response = await llm.ainvoke([system, *state["messages"]])
    return {"messages": [response], "summary": response.content}


def build_research_graph() -> None:
    graph = StateGraph(ResearchState)
    graph.add_node("research",  _research_node)
    graph.add_node("summarize", _summarize_node)

    graph.add_edge(START,      "research")
    graph.add_edge("research", "summarize")
    graph.add_edge("summarize", END)

    GraphRegistry.register(
        "research_chain",
        graph.compile(checkpointer=get_checkpointer()),
    )
