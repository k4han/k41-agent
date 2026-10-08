"""Channel-only history retention with safe message boundaries."""

from collections.abc import Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from agent.modules.workflows.message_history import normalize_messages_for_chat_model


def trim_channel_history(
    messages: list[BaseMessage],
    max_tokens: int,
    token_counter: Callable[[list[BaseMessage]], int] | None = None,
) -> list[BaseMessage]:
    from agent.modules.conversations import is_valid_message_sequence

    def counter(items):
        if token_counter is not None:
            return token_counter(items)
        return count_tokens_approximately(normalize_messages_for_chat_model(items))

    if not messages or (counter(messages) <= max_tokens and isinstance(messages[0], HumanMessage)):
        return messages
    trimmed = trim_messages(
        messages, strategy="last", token_counter=counter,
        max_tokens=max_tokens, start_on="human", allow_partial=False,
        include_system=False,
    )
    if trimmed and is_valid_message_sequence(trimmed):
        return trimmed
    # An oversized latest turn must remain consumable, including its tool results.
    for boundary_type in (HumanMessage, AIMessage):
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], boundary_type):
                tail = messages[index:]
                if is_valid_message_sequence(tail):
                    return tail
    return messages


