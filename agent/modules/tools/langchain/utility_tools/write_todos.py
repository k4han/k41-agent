from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field


TodoStatus = Literal["pending", "in_progress", "completed"]


class TodoItem(BaseModel):
    """A single todo item for the current agent run."""

    content: str = Field(
        ...,
        min_length=1,
        description="Specific task description.",
    )
    status: TodoStatus = Field(
        ...,
        description="Current task status.",
    )


class WriteTodosInput(BaseModel):
    """Input schema for the write_todos tool."""

    todos: list[TodoItem] = Field(
        ...,
        description="Complete replacement todo list for the current work session.",
    )


@tool(args_schema=WriteTodosInput)
def write_todos(
    todos: list[TodoItem],
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> Command[Any]:
    """Create or update the structured todo list for the current work session."""
    normalized_todos = [todo.model_dump() for todo in todos]
    return Command(
        update={
            "todos": normalized_todos,
            "messages": [
                ToolMessage(
                    content=f"Updated todo list to {normalized_todos}",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )
