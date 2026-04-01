"""call_agent tool — enables sub-agent invocation within workflow graphs."""

from __future__ import annotations

import logging
import uuid

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime

from agent.modules.workflows.infrastructure.langgraph.run_config import (
    DEFAULT_WORKING_DIR,
    WorkflowContext,
    get_context_value,
)

logger = logging.getLogger(__name__)


def _make_subagent_thread_id(runtime: ToolRuntime[WorkflowContext], sub_agent: str) -> str:
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
    runtime: ToolRuntime[WorkflowContext],
) -> str:
    """Invoke a sub-agent to handle a specific task."""
    from langchain_core.messages import HumanMessage

    from agent.modules.agents.public import get_catalog_service
    from agent.modules.workflows.public import (
        get_workflow_graph,
        make_run_config,
        make_run_context,
    )

    caller_agent_name = get_context_value(runtime.context, "agent_name", "default")
    inherited_working_dir = get_context_value(
        runtime.context,
        "working_dir",
        DEFAULT_WORKING_DIR,
    )

    catalog = get_catalog_service()

    if not catalog.validate_call(caller_agent_name, sub_agent):
        return f"[error] not allowed to call agent '{sub_agent}'."

    target_config = catalog.get_agent(sub_agent)
    if target_config is None:
        return f"[error] agent config not found for '{sub_agent}'."

    try:
        graph = get_workflow_graph(target_config.graph_type)
    except ValueError:
        return f"[error] graph type '{target_config.graph_type}' not registered."

    context = make_run_context(
        service_type=target_config.service_type,
        working_dir=inherited_working_dir,
        max_context_tokens=target_config.max_context_tokens,
        agent_name=sub_agent,
        allowed_tool_names=target_config.tools if target_config.tools else None,
    )
    config = make_run_config(
        thread_id=_make_subagent_thread_id(runtime, sub_agent),
    )

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=task)]},
            config=config,
            context=context,
        )
        messages = result.get("messages", [])
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if content and msg.__class__.__name__ == "AIMessage":
                return str(content)
        return "(empty response)"
    except Exception as e:
        logger.exception(
            "Sub-agent '%s' failed for caller '%s' and task: %s",
            sub_agent,
            caller_agent_name,
            task,
        )
        return f"[error] sub-agent '{sub_agent}' failed: {e}"


def make_call_agent_tool(_agent_name: str) -> BaseTool:
    """Deprecated compatibility wrapper.

    call_agent now reads the caller agent from ToolRuntime instead of a closure.
    """
    return call_agent
