from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory


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


WRITE_TODOS_TOOL_DESCRIPTION = (
    "Create and manage a structured todo list for the current work session. "
    "Use this for complex multi-step tasks where progress tracking helps, and "
    "replace the entire list with the current set of useful todos."
)


def _write_todos(
    runtime: ToolRuntime[Any, Any],
    todos: list[TodoItem],
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


async def _awrite_todos(
    runtime: ToolRuntime[Any, Any],
    todos: list[TodoItem],
) -> Command[Any]:
    """Async wrapper for write_todos."""
    return _write_todos(runtime, todos)


write_todos = StructuredTool.from_function(
    name="write_todos",
    description=WRITE_TODOS_TOOL_DESCRIPTION,
    func=_write_todos,
    coroutine=_awrite_todos,
    args_schema=WriteTodosInput,
    infer_schema=False,
)

register_tool(
    category=ToolCategory.UTILITY,
    capabilities=[ToolCapability.MUTATES_STATE],
    tags=["planning", "todo"],
)(write_todos)
