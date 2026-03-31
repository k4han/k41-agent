from collections.abc import Callable

from langchain_core.messages import BaseMessage, RemoveMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langgraph.runtime import Runtime

from agent.modules.workflows.infrastructure.langgraph.run_config import (
    WorkflowContext,
    get_context_value,
)


def _safe_positive_int(value, default_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default_value
    return max(1, parsed)


def make_prepare_context_node(
    default_max_context_tokens: int = 50_000,
    token_counter: Callable[[list[BaseMessage]], int] | None = None,
):
    """
    Trim messages before entering first LLM step with token budget.
    Defaults to 50,000 tokens and keeps the latest valid window.
    """

    def prepare_context_node(state, runtime: Runtime[WorkflowContext]):
        messages: list[BaseMessage] = state.get("messages", [])
        if not messages:
            return {}

        max_context_value = get_context_value(
            runtime.context, "max_context_tokens", None
        )

        max_context_tokens = _safe_positive_int(
            max_context_value,
            default_max_context_tokens,
        )
        counter = token_counter or count_tokens_approximately
        total_tokens = counter(messages)

        if total_tokens <= max_context_tokens and messages[0].type == "human":
            return {}

        trimmed = trim_messages(
            messages,
            strategy="last",
            token_counter=counter,
            max_tokens=max_context_tokens,
            start_on="human",
            include_system=False,
            allow_partial=False,
        )

        if not trimmed:
            last_message = messages[-1]
            removal_ids = {m.id for m in messages if m.id and m.id != last_message.id}

            if not removal_ids:
                return {}
            return {"messages": [RemoveMessage(id=mid) for mid in removal_ids]}

        keep_ids = {m.id for m in trimmed if m.id}
        removal_ids = {m.id for m in messages if m.id and m.id not in keep_ids}

        if not removal_ids:
            return {}

        return {"messages": [RemoveMessage(id=mid) for mid in removal_ids]}

    return prepare_context_node
