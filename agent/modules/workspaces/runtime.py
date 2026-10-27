"""Provider-aware runtime observations and ownership checks."""

from dataclasses import dataclass

from sqlalchemy import select


@dataclass(frozen=True)
class RuntimeObservation:
    state: str
    can_resume: bool = False
    needs_replacement: bool = False


def runtime_observation(backend: str, state: str) -> RuntimeObservation:
    return RuntimeObservation(state,
        can_resume=backend == "daytona" and state in {"stopped", "archived"},
        needs_replacement=state == "destroyed" or (backend == "modal" and state in {"stopped", "finished"}))


async def runtime_is_owned(ref) -> bool:
    from agent.modules.projects.models import WorkspaceRuntime
    from agent.modules.projects.workspaces import _session
    from agent.modules.workspaces.models import ThreadWorkspace
    from agent.modules.workspaces.refs import WorkspaceRef
    session = await _session()
    async with session:
        runtimes = (await session.execute(select(WorkspaceRuntime).where(WorkspaceRuntime.status != "destroyed"))).scalars().all()
        if any((current := WorkspaceRef.model_validate_json(row.execution_json)).backend == ref.backend
               and current.locator == ref.locator for row in runtimes):
            return True
        return (await session.execute(select(ThreadWorkspace.id).where(
            ThreadWorkspace.execution_backend == ref.backend, ThreadWorkspace.execution_locator == ref.locator,
        ).limit(1))).scalar_one_or_none() is not None
