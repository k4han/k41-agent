"""Reserve a durable workspace for an entire agent run on every channel."""

from contextlib import asynccontextmanager
from functools import wraps
import inspect


@asynccontextmanager
async def workspace_run(arguments):
    from agent.bootstrap.container import get_active_container
    container = get_active_container()
    if container is None or not container._persistence_ready:
        yield
        return
    from agent.modules.projects import get_project_workspace_service
    projects = get_project_workspace_service(container)
    requested = arguments.get("workspace") or arguments.get("working_dir")
    binding = await projects.find_binding(requested, thread_id=arguments.get("thread_id"))
    if binding is None and requested is None:
        from agent.modules.workspaces import resolve_workspace_ref
        binding = await projects.find_binding(resolve_workspace_ref(None))
    if binding is None:
        yield
        return
    with projects.switcher.run_lease(binding.project_workspace_id):
        arguments["workspace"] = binding.model_dump()
        yield


def guard_workspace_run(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def guarded(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        async with workspace_run(bound.arguments):
            async for event in function(*bound.args, **bound.kwargs):
                yield event

    return guarded
