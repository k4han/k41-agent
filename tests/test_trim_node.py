from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from agent.modules.workflows.infrastructure.langgraph.nodes.trim import (
    make_prepare_context_node,
)


def _apply_removals(messages, updates):
    remove_ids = {
        update.id
        for update in updates
        if isinstance(update, RemoveMessage) and update.id
    }
    return [message for message in messages if message.id not in remove_ids]


def test_prepare_context_trims_to_token_budget():
    node = make_prepare_context_node(default_max_context_tokens=21, token_counter=len)

    messages = []
    for idx in range(12):
        messages.append(HumanMessage(content=f"user-{idx}", id=f"h-{idx}"))
        messages.append(AIMessage(content=f"assistant-{idx}", id=f"a-{idx}"))
    messages.append(HumanMessage(content="latest-user", id="h-latest"))

    result = node(
        {"messages": messages},
        SimpleNamespace(context={"max_context_tokens": 21}),
    )

    updates = result["messages"]
    assert updates

    remaining = _apply_removals(messages, updates)
    assert len(remaining) == 21
    assert remaining[0].type == "human"
    assert remaining[-1].type == "human"
    assert remaining[-1].content == "latest-user"


def test_prepare_context_removes_non_human_prefix_when_needed():
    node = make_prepare_context_node(default_max_context_tokens=10, token_counter=len)

    messages = [
        AIMessage(content="prefix-ai", id="a-prefix"),
        HumanMessage(content="user-1", id="h-1"),
        AIMessage(content="assistant-1", id="a-1"),
        HumanMessage(content="latest-user", id="h-latest"),
    ]

    result = node(
        {"messages": messages},
        SimpleNamespace(context={"max_context_tokens": 10}),
    )

    updates = result["messages"]
    removed_ids = {update.id for update in updates if isinstance(update, RemoveMessage)}
    assert "a-prefix" in removed_ids

    remaining = _apply_removals(messages, updates)
    assert remaining[0].type == "human"


def test_prepare_context_noop_when_window_is_already_valid():
    node = make_prepare_context_node(default_max_context_tokens=10, token_counter=len)

    messages = [
        HumanMessage(content="user-1", id="h-1"),
        AIMessage(content="assistant-1", id="a-1"),
        HumanMessage(content="latest-user", id="h-latest"),
    ]

    result = node(
        {"messages": messages},
        SimpleNamespace(context={"max_context_tokens": 10}),
    )

    assert result == {}
