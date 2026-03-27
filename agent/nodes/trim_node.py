from collections.abc import Callable

from langchain_core.messages import BaseMessage, RemoveMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.runnables import RunnableConfig


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

    def prepare_context_node(state, config: RunnableConfig):
        messages: list[BaseMessage] = state.get("messages", [])
        if not messages:
            return {}

        cfg = config.get("configurable", {})
        max_context_tokens = _safe_positive_int(
            cfg.get("max_context_tokens"),
            default_max_context_tokens,
        )
        counter = token_counter or count_tokens_approximately
        total_tokens = counter(messages)
        # print(f"Counting tokens for messages. Total tokens: {total_tokens}")

        # Không cần trim nếu còn đủ chỗ
        if total_tokens <= max_context_tokens:
            return {}

        trimmed = trim_messages(
            messages,
            strategy="last",
            token_counter=counter,
            max_tokens=int(max_context_tokens * 0.6),
            start_on="human",
            include_system=False,
            allow_partial=False,
        )
        
        # new human message > max_context_tokens
        if not trimmed:
            last_message = messages[-1]
            
            removal_ids = {m.id for m in messages if m.id and m.id != last_message.id}

            if not removal_ids:
                return {}
            return {"messages": [RemoveMessage(id=mid) for mid in removal_ids]}
        else:
            keep_ids = {m.id for m in trimmed if m.id}
            removal_ids = {m.id for m in messages if m.id and m.id not in keep_ids}

        if not removal_ids:
            return {}

        print(
            f"Trimming context to fit {max_context_tokens} tokens. "
            f"Original: {len(messages)}, Kept: {len(messages) - len(removal_ids)}, "
            f"Removed: {len(removal_ids)}"
        )

        return {"messages": [RemoveMessage(id=mid) for mid in removal_ids]}

    return prepare_context_node
