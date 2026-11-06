"""Find which graph owns a thread, for code that inspects its checkpoint state.

Every graph shares one checkpointer, but a graph only understands its own nodes.
A react graph looking at an ACP checkpoint reports no tasks and no interrupts.

A thread's family is stored when its first run starts, because its agent name can
change later and the agent's card can be edited or deleted.
"""

from __future__ import annotations

import logging

from agent.modules.workflows.constants import (
    ACP_AGENT_GRAPH_TYPE,
    GRAPH_FAMILY_ACP,
    GRAPH_FAMILY_MODEL,
    REACT_AGENT_GRAPH_TYPE,
)

logger = logging.getLogger(__name__)


def graph_family_for(graph_type: str) -> str:
    return GRAPH_FAMILY_ACP if graph_type == ACP_AGENT_GRAPH_TYPE else GRAPH_FAMILY_MODEL


async def thread_graph_family(thread_id: str) -> str:
    """Return the thread's family, or an empty string if any agent may still claim it.

    Raises if the thread cannot be loaded, so callers enforcing the boundary fail closed.
    """
    from agent.modules.agents import get_catalog_service
    from agent.modules.conversations import get_conversation_thread

    thread = await get_conversation_thread(thread_id)
    if not thread:
        return ""
    stored = str(thread.get("graph_family") or "")
    if stored:
        return stored
    # Threads last run before families were stored only have their agent's name.
    agent_name = str(thread.get("agent_name") or "")
    if not agent_name:
        return ""
    agent = get_catalog_service().get_agent(agent_name)
    return graph_family_for(agent.graph_type) if agent is not None else GRAPH_FAMILY_MODEL


async def thread_is_acp(thread_id: str) -> bool:
    """Whether the thread belongs to an ACP agent, for best-effort inspection."""
    try:
        return await thread_graph_family(thread_id) == GRAPH_FAMILY_ACP
    except Exception as exc:
        logger.debug("Could not resolve the graph family of thread %s: %s", thread_id, exc)
        return False


async def graph_name_for_thread(thread_id: str) -> str:
    return ACP_AGENT_GRAPH_TYPE if await thread_is_acp(thread_id) else REACT_AGENT_GRAPH_TYPE
