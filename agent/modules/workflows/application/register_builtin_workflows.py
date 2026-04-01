from agent.modules.workflows.infrastructure.langgraph.graphs import (
    build_react_graph,
    build_research_graph,
    build_router_graph,
)
from agent.modules.workflows.infrastructure.langgraph.graphs.agent_scan import (
    scan_agents_from_md,
)


def register_builtin_workflows() -> None:
    build_react_graph()
    build_research_graph()
    build_router_graph()
    scan_agents_from_md()
    print("[Registry] All graphs ready.")
