"""call_agent tool — enables sub-agent invocation within workflow graphs."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.runtime.context import get_context_value

logger = logging.getLogger(__name__)


def _make_subagent_thread_id(runtime: ToolRuntime[Any, Any], sub_agent: str) -> str:
    configurable = runtime.config.get("configurable", {})
    parent_thread_id = ""
    if isinstance(configurable, dict):
        parent_thread_id = str(configurable.get("thread_id", "") or "").strip()
    suffix = uuid.uuid4().hex[:8]
    if parent_thread_id:
        return f"{parent_thread_id}:sub:{sub_agent}:{suffix}"
    return f"sub_{sub_agent}_{suffix}"


@tool
async def call_agent(
    task: str,
    sub_agent: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Invoke a sub-agent to handle a specific task."""
    from agent.modules.agent_runtime import run_agent_full
    from agent.modules.agents import get_catalog_service

    caller_agent_name = get_context_value(runtime.context, "agent_name", "default")
    inherited_workspace = get_context_value(runtime.context, "workspace", None)
    inherited_working_dir = get_context_value(runtime.context, "working_dir", None)
    workspace_ref = (
        inherited_workspace if inherited_workspace is not None else inherited_working_dir
    )
    inherited_provider = get_context_value(runtime.context, "provider", None)
    inherited_model = get_context_value(runtime.context, "model", None)

    catalog = get_catalog_service()

    if not catalog.validate_call(caller_agent_name, sub_agent):
        return f"[error] not allowed to call agent '{sub_agent}'."

    if catalog.get_agent(sub_agent) is None:
        return f"[error] agent config not found for '{sub_agent}'."

    sub_thread_id = _make_subagent_thread_id(runtime, sub_agent)
    try:
        result = await run_agent_full(
            user_input=task,
            thread_id=sub_thread_id,
            agent_name=sub_agent,
            workspace=workspace_ref,
            provider=inherited_provider,
            model=inherited_model,
        )
        return result or "(empty response)"
    except Exception as e:
        logger.exception(
            "Sub-agent '%s' failed for caller '%s' and task: %s",
            sub_agent,
            caller_agent_name,
            task,
        )
        return f"[error] sub-agent '{sub_agent}' failed: {e}"
