from typing import NotRequired

from langgraph.graph import MessagesState


class BaseState(MessagesState):
    """Base state for all workflow graphs."""

    todos: NotRequired[list[dict[str, str]]]
    active_skills: NotRequired[dict[str, dict]]
    processed_skill_messages: NotRequired[list[str]]
