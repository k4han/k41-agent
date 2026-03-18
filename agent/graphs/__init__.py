# agent/graphs/__init__.py

from agent.graphs.chat_agent     import build_chat_graph
from agent.graphs.coding_agent   import build_coding_graph
from agent.graphs.research_chain import build_research_graph
from agent.graphs.router         import build_router_graph


def setup_all_graphs() -> None:
    """
    Build và register tất cả graphs.
    Gọi 1 lần duy nhất khi app khởi động.
    """
    build_chat_graph()
    build_coding_graph()
    build_research_graph()
    build_router_graph()   # Router phải build sau cùng vì nó dùng các graph kia
    print("[Registry] All graphs ready.")
