from typing import Annotated, NotRequired

from langgraph.graph import MessagesState

from agent.shared.async_subagents import merge_event_cursors, merge_task_references


class BaseState(MessagesState):
    """Base state for all workflow graphs."""

    todos: NotRequired[list[dict[str, str]]]
    active_skills: NotRequired[dict[str, dict]]
    processed_skill_messages: NotRequired[list[str]]
    async_tasks: Annotated[dict[str, dict], merge_task_references]
    async_event_cursors: Annotated[dict[str, int], merge_event_cursors]
