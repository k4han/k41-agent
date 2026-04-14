from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent.modules.workflows.infrastructure.langgraph.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.infrastructure.langgraph.compiled_registry import (
    GraphRegistry,
)
from agent.modules.workflows.infrastructure.langgraph.nodes.trim import (
    make_prepare_context_node,
)
from agent.modules.workflows.infrastructure.langgraph.run_config import WorkflowContext
from agent.modules.workflows.infrastructure.langgraph.state.extensions import (
    ResearchState,
)
from agent.modules.providers.public import get_chat_model
from agent.shared.infrastructure.parsing import extract_final_text_content


async def _research_node(state: ResearchState, config: RunnableConfig):
    """Bước 1: Thu thập thông tin theo yêu cầu."""
    llm = get_chat_model()
    system = SystemMessage(content=(
        "Bạn là research assistant. "
        "Hãy phân tích yêu cầu và liệt kê các nguồn thông tin cần tìm hiểu."
    ))
    response = await llm.ainvoke([system, *state["messages"]])
    return {"messages": [response]}


async def _summarize_node(state: ResearchState, config: RunnableConfig):
    """Bước 2: Tổng hợp kết quả nghiên cứu."""
    llm = get_chat_model()
    system = SystemMessage(content=(
        "Dựa trên thông tin đã thu thập, hãy tổng hợp thành báo cáo ngắn gọn, "
        "có cấu trúc rõ ràng với: Tóm tắt, Điểm chính, Kết luận."
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
