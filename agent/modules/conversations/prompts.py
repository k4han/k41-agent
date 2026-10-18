"""Conversation input templates, separate from agent card behavior."""

TITLE_INPUT_TEMPLATE = "User message:\n{message}"
COMPACTION_INPUT_TEMPLATE = "Earlier Conversation History to Summarize:\n\n{history}"
COMPACTION_BUDGET_TEMPLATE = " Keep the summary within {tokens} tokens."
COMPACTED_CONTEXT_TEMPLATE = (
    "[Conversation Context Summary]\n"
    "The earlier conversation before this point was compacted to conserve context:\n\n"
    "{summary}\n\n"
    "Continue the active request using this context."
)
COMPACTED_CONTEXT_ACKNOWLEDGEMENT = "I have reviewed the conversation summary and will continue the active request."
