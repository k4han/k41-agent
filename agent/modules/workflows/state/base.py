from typing import NotRequired

from langgraph.graph import MessagesState


class BaseState(MessagesState):
    """Base state for all workflow graphs."""

    todos: NotRequired[list[dict[str, str]]]
