"""call_agent tool — enables sub-agent invocation within react_agent graphs."""

from __future__ import annotations

import logging
from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)


def make_call_agent_tool(agent_name: str) -> BaseTool:
    """Factory: create a call_agent tool bound to a specific caller agent.

    The returned tool validates sub_agents permission and invokes the target graph.
    Imports are deferred inside the closure to avoid circular imports.
    """

    @tool
    async def call_agent(task: str, sub_agent: str) -> str:
        """Invoke a sub-agent to handle a specific task.

        Args:
            task: The task or question to delegate.
            sub_agent: Name of the sub-agent to call.
        """
        from langchain_core.messages import HumanMessage

        from agent.modules.agents.public import get_catalog_service
        from agent.modules.workflows.public import (
            get_workflow_graph,
            make_run_config,
            make_run_context,
        )

        catalog = get_catalog_service()

        # Validate caller -> callee permission
        if not catalog.validate_call(agent_name, sub_agent):
            return f"[error] not allowed to call agent '{sub_agent}'."

        # Resolve target agent config
        target_config = catalog.get_agent(sub_agent)
        if target_config is None:
            return f"[error] agent config not found for '{sub_agent}'."

        # Resolve the graph for this agent's graph_type
        # The target uses the base graph_type (e.g. "react_agent"), not the agent name.
        try:
            graph = get_workflow_graph(target_config.graph_type)
        except ValueError:
            return f"[error] graph type '{target_config.graph_type}' not registered."

        from agent.modules.workflows.infrastructure.langgraph.run_config import (
            DEFAULT_WORKING_DIR,
        )

        # Build context from target agent config
        context = make_run_context(
            service_type=target_config.service_type,
            working_dir=DEFAULT_WORKING_DIR,
            max_context_tokens=target_config.max_context_tokens,
            agent_name=sub_agent,
            allowed_tool_names=target_config.tools if target_config.tools else None,
        )
        import uuid

        config = make_run_config(thread_id=f"sub_{sub_agent}_{uuid.uuid4().hex[:8]}")

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
            logger.exception("Sub-agent '%s' failed for task: %s", sub_agent, task)
            return f"[error] sub-agent '{sub_agent}' failed: {e}"

    # Set tool name to "call_agent" for consistent LLM interface
    call_agent.name = "call_agent"  # type: ignore[attr-defined]
    return call_agent  # type: ignore[return-value]
