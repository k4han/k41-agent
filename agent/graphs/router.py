# agent/graphs/router.py
# Router graph: nhận user input, tự phân loại workflow phù hợp

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END

from agent.registry import GraphRegistry
from agent.state.base import BaseState
from agent.providers.llm import get_llm


ROUTER_SYSTEM = """Bạn là router agent. Nhiệm vụ của bạn là phân loại yêu cầu người dùng 
vào đúng workflow. Chỉ trả lời bằng đúng 1 trong các giá trị sau (không giải thích):
- chat_agent      : hỏi đáp thông thường, câu hỏi kiến thức
- coding_agent    : viết code, đọc/ghi file, chạy lệnh bash
- research_chain  : nghiên cứu, tổng hợp thông tin, phân tích

Yêu cầu người dùng: {user_input}"""


def _router_node(state: BaseState, config: RunnableConfig) -> dict:
    """Phân loại yêu cầu và chuyển sang graph phù hợp."""
    user_input = state["messages"][-1].content
    llm        = get_llm()

    response = llm.invoke([
        SystemMessage(content=ROUTER_SYSTEM.format(user_input=user_input))
    ])

    workflow = response.content.strip().lower()
    valid    = {"chat_agent", "coding_agent", "research_chain"}
    if workflow not in valid:
        workflow = "chat_agent"  # fallback

    # Lấy graph đã đăng ký và chạy tiếp
    target_graph = GraphRegistry.get(workflow)
    result       = target_graph.invoke(
        {"messages": state["messages"]},
        config=config,
    )
    return {"messages": result["messages"]}


def build_router_graph() -> None:
    graph = StateGraph(BaseState)
    graph.add_node("router", _router_node)
    graph.add_edge(START, "router")
    graph.add_edge("router", END)

    GraphRegistry.register("router", graph.compile())
