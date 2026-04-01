from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.modules.workflows.infrastructure.langgraph.checkpoint import (
    get_checkpointer,
)
from agent.modules.workflows.infrastructure.langgraph.compiled_registry import (
    GraphRegistry,
)
from agent.modules.workflows.infrastructure.langgraph.run_config import (
    WorkflowContext,
)
from agent.modules.workflows.infrastructure.langgraph.state.base import BaseState
from agent.modules.providers.public import get_chat_model

ROUTER_SYSTEM_TEMPLATE = """You are a router agent. Your task is to classify user requests
into the correct workflow. Respond with exactly one workflow name from the list below (no explanation):
{workflow_options}

User request: {user_input}"""


def _workflow_description(name: str, description: str) -> str:
    if description:
        return description
    return f"{name.replace('_', ' ').replace('-', ' ')} workflow"


def _build_router_system(user_input: str) -> tuple[str, dict[str, str]]:
    workflows = GraphRegistry.routeable_workflows()
    if not workflows:
        raise RuntimeError("No routeable workflows are registered.")

    workflow_options = "\n".join(
        f"- {name}: {_workflow_description(name, description)}"
        for name, description in workflows.items()
    )
    return (
        ROUTER_SYSTEM_TEMPLATE.format(
            workflow_options=workflow_options,
            user_input=user_input,
        ),
        workflows,
    )


def _normalize_workflow_name(raw_value: object) -> str:
    content = str(raw_value).strip()
    if not content:
        return ""

    first_line = content.splitlines()[0].strip()
    first_line = first_line.lstrip("-* ").strip()
    return first_line.strip("`'\"").lower()


def _default_workflow(workflows: dict[str, str]) -> str:
    if "react_agent" in workflows:
        return "react_agent"
    return next(iter(workflows))


async def _router_node(
    state: BaseState,
    config: RunnableConfig,
    runtime: Runtime[WorkflowContext],
) -> dict:
    """Classify request and route to appropriate graph."""
    user_input = state["messages"][-1].content
    llm = get_chat_model()
    router_system, workflows = _build_router_system(user_input)

    response = await llm.ainvoke([
        SystemMessage(content=router_system)
    ])

    workflow = _normalize_workflow_name(response.content)
    if workflow not in workflows:
        workflow = _default_workflow(workflows)

    target_graph = GraphRegistry.get(workflow)
    result = await target_graph.ainvoke(
        {"messages": state["messages"]},
        config=config,
        context=runtime.context,
    )
    return {"messages": result["messages"]}


def build_router_graph() -> None:
    graph = StateGraph(BaseState, context_schema=WorkflowContext)
    graph.add_node("router", _router_node)
    graph.add_edge(START, "router")
    graph.add_edge("router", END)

    GraphRegistry.register(
        "router",
        graph.compile(checkpointer=get_checkpointer()),
        description="internal workflow router",
        routeable=False,
    )
