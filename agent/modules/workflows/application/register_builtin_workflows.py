from agent.modules.workflows.infrastructure.langgraph.graphs import (
    build_react_graph,
    build_research_graph,
    build_router_graph,
)


def register_builtin_workflows() -> None:
    build_react_graph()
    build_research_graph()
    build_router_graph()
    print("[Registry] All graphs ready.")
