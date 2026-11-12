"""delegate_task tool — enables sub-agent invocation within workflow graphs."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.permissions.risk import RiskLevel
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.context import get_context_value
from agent.modules.usage import usage_context_from_config

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


@register_tool(risk=RiskLevel.MUTATING, may_interrupt=False,
    category=ToolCategory.AGENT,
    capabilities=[
        ToolCapability.MUTATES_STATE,
        ToolCapability.ASYNC_ONLY,
        ToolCapability.REQUIRES_THREAD,
        ToolCapability.NETWORK,
    ],
    tags=["subagent", "delegation"],
)
@tool
async def delegate_task(
    task: str,
    sub_agent: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Delegate a task to a specialized sub-agent and return its result.

    Use the exact sub_agent name from the callable agents catalog in this tool description.
    Provide a self-contained task with the context and expected result the sub-agent needs.
    """
    from agent.modules.tools.coding.adapter import invocation_context
    from agent.modules.agent_runtime import run_agent_full
    from agent.modules.agents import get_catalog_service
    from agent.modules.browser import release_delegated_browser

    caller_agent_name = get_context_value(runtime.context, "agent_name", "default")
    inherited_workspace = get_context_value(runtime.context, "workspace", None)
    inherited_working_dir = get_context_value(runtime.context, "working_dir", None)
    workspace_ref = (
        inherited_workspace if inherited_workspace is not None else inherited_working_dir
    )
    inherited_provider = get_context_value(runtime.context, "provider", None)
    inherited_model = get_context_value(runtime.context, "model", None)
    inherited_allowed_skills = get_context_value(
        runtime.context,
        "allowed_skill_names",
        None,
    )

    catalog = get_catalog_service()

    if not catalog.validate_call(caller_agent_name, sub_agent):
        raise ToolError(
            ToolErrorCode.PERMISSION_DENIED,
            f"not allowed to call agent '{sub_agent}'.",
        )

    if catalog.get_agent(sub_agent) is None:
        raise ToolError(
            ToolErrorCode.NOT_FOUND,
            f"agent config not found for '{sub_agent}'.",
        )

    sub_thread_id = _make_subagent_thread_id(runtime, sub_agent)
    delegation_id = uuid.uuid4().hex
    parent_thread_id = str(runtime.config.get("configurable", {}).get("thread_id") or "")
    metadata = {"k41_parent_thread_id": parent_thread_id,
        "k41_parent_tool_call_id": str(getattr(runtime, "tool_call_id", "") or ""),
        "k41_delegation_id": delegation_id, "k41_agent_name": sub_agent}
    parent_messages = (getattr(runtime, "state", None) or {}).get("messages", [])
    if parent_messages:
        metadata["k41_parent_message_id"] = str(getattr(parent_messages[-1], "id", "") or "")

    def forward(event):
        writer = getattr(runtime, "stream_writer", None)
        if callable(writer):
            writer({"type": "subagent_event", "event": event, "agent_name": sub_agent,
                "delegation_id": delegation_id, "thread_id": sub_thread_id,
                "tool_call_id": metadata["k41_parent_tool_call_id"]})
    try:
        result = await run_agent_full(
            user_input=task,
            thread_id=sub_thread_id,
            agent_name=sub_agent,
            permission_mode=invocation_context(runtime).permission_mode,
            workspace=workspace_ref,
            provider=inherited_provider,
            model=inherited_model,
            browser_owner_id=get_context_value(runtime.context, "browser_owner_id", None),
            allowed_skill_names=inherited_allowed_skills,
            usage_context=usage_context_from_config(runtime.config).to_dict(),
            _checkpoint_metadata=metadata,
            _event_writer=forward,
        )
        return result or "(empty response)"
    except Exception as e:
        logger.exception(
            "Sub-agent '%s' failed for caller '%s' and task: %s",
            sub_agent,
            caller_agent_name,
            task,
        )
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"sub-agent '{sub_agent}' failed: {e}",
        ) from e
    finally:
        await release_delegated_browser(sub_thread_id)
