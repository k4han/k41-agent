from agent.modules.workflows.run_config import (
    DEFAULT_WORKING_DIR,
    make_context as make_run_context,
    make_config as make_run_config,
)
from agent.modules.workflows.constants import (
    REACT_AGENT_GRAPH_TYPE,
    ROUTER_GRAPH_TYPE,
)


def register_builtin_workflows() -> None:
    from agent.modules.workflows.register_builtin_workflows import (
        register_builtin_workflows as _register_builtin_workflows,
    )

    _register_builtin_workflows()


def get_workflow_graph(name: str):
    from agent.modules.workflows.registry import GraphRegistry

    return GraphRegistry.get(name)


def list_registered_workflows() -> list[str]:
    from agent.modules.workflows.registry import GraphRegistry

    return list(GraphRegistry.all().keys())


async def initialize_checkpointer() -> None:
    from agent.modules.workflows.checkpoint.store import (
        initialize_checkpointer as _initialize_checkpointer,
    )

    await _initialize_checkpointer()


async def close_checkpointer() -> None:
    from agent.modules.workflows.checkpoint.store import (
        close_checkpointer as _close_checkpointer,
    )

    await _close_checkpointer()


def get_checkpointer():
    from agent.modules.workflows.checkpoint.store import get_checkpointer as _get_checkpointer

    return _get_checkpointer()


async def delete_workflow_thread(thread_id: str) -> None:
    from agent.modules.workflows.checkpoint.store import get_checkpointer

    checkpointer = get_checkpointer()
    if checkpointer is not None:
        await checkpointer.adelete_thread(thread_id)


__all__ = [
    "DEFAULT_WORKING_DIR",
    "delete_workflow_thread",
    "get_workflow_graph",
    "initialize_checkpointer",
    "get_checkpointer",
    "list_registered_workflows",
    "make_run_context",
    "make_run_config",
    "register_builtin_workflows",
    "close_checkpointer",
    "REACT_AGENT_GRAPH_TYPE",
    "ROUTER_GRAPH_TYPE",
]
