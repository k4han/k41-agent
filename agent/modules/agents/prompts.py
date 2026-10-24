"""Render reusable agent card instructions with runtime values."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.modules.workspaces import WorkspaceRef


def get_agent_prompt_template(name: str) -> str:
    from agent.modules.agents import get_catalog_service

    catalog = get_catalog_service()
    card = catalog.get_agent(name)
    if card is None:
        get_card = getattr(catalog, "get_agent_card", None)
        card = get_card(name) if callable(get_card) else None
    if card is None or not card.system_prompt.strip():
        raise RuntimeError(f"Agent '{name}' must define a non-empty system prompt.")
    return card.system_prompt


async def render_agent_prompt(
    name: str,
    *,
    values: dict[str, Any] | None = None,
    working_dir: str = "",
    workspace: str | WorkspaceRef = "",
    prompt_variables: dict[str, str] | None = None,
) -> str:
    """Resolve card instructions against the supplied execution workspace."""
    from agent.modules.prompt_variables import get_runtime_prompt_variable_values
    from agent.modules.prompt_variables import get_runtime_system_variables
    from agent.modules.workspaces import WorkspaceRef
    from agent.modules.workflows import (
        get_system_default_variables, replace_known_prompt_placeholders, resolve_prompt_variables,
    )

    template = get_agent_prompt_template(name)
    defaults = (
        await get_runtime_system_variables(workspace)
        if isinstance(workspace, WorkspaceRef)
        else get_system_default_variables(working_dir, workspace)
    )
    variables = {
        **defaults,
        **(prompt_variables if prompt_variables is not None else await get_runtime_prompt_variable_values()),
        **{key: str(value) for key, value in (values or {}).items()},
    }
    return replace_known_prompt_placeholders(
        resolve_prompt_variables(template, variables), variables,
    )
