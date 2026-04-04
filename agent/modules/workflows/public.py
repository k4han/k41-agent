from langgraph.graph.state import CompiledStateGraph

from agent.modules.workflows.application.register_builtin_workflows import (
    register_builtin_workflows,
)
from agent.modules.workflows.infrastructure.langgraph.compiled_registry import (
    GraphRegistry,
)
from agent.modules.workflows.infrastructure.langgraph.run_config import (
    DEFAULT_WORKING_DIR,
    make_context as make_run_context,
    make_config as make_run_config,
)
from agent.modules.workflows.infrastructure.langgraph.checkpoint.store import (
    close_checkpointer,
    initialize_checkpointer,
)


def get_workflow_graph(name: str) -> CompiledStateGraph:
    return GraphRegistry.get(name)


def list_registered_workflows() -> list[str]:
    return list(GraphRegistry.all().keys())


async def delete_workflow_thread(thread_id: str) -> None:
    from agent.modules.workflows.infrastructure.langgraph.checkpoint.store import (
        get_checkpointer,
    )

    checkpointer = get_checkpointer()
    if checkpointer and hasattr(checkpointer, "adelete_thread"):
        await checkpointer.adelete_thread(thread_id)


__all__ = [
    "DEFAULT_WORKING_DIR",
    "delete_workflow_thread",
    "get_workflow_graph",
    "initialize_checkpointer",
    "list_registered_workflows",
    "make_run_context",
    "make_run_config",
    "register_builtin_workflows",
    "close_checkpointer",
]
