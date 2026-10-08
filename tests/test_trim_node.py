from langchain_core.messages import AIMessage, HumanMessage

from agent.modules.workflows.nodes.trim import make_prepare_context_node, trim_channel_history


def test_channel_history_trims_to_token_budget():
    messages = []
    for idx in range(12):
        messages.append(HumanMessage(content=f"user-{idx}", id=f"h-{idx}"))
        messages.append(AIMessage(content=f"assistant-{idx}", id=f"a-{idx}"))
    messages.append(HumanMessage(content="latest-user", id="h-latest"))

    remaining = trim_channel_history(messages, max_tokens=21, token_counter=len)
    assert len(remaining) == 21
    assert remaining[0].type == "human"
    assert remaining[-1].content == "latest-user"


def test_channel_history_removes_non_human_prefix():
    messages = [
        AIMessage(content="prefix-ai", id="a-prefix"),
        HumanMessage(content="user-1", id="h-1"),
        AIMessage(content="assistant-1", id="a-1"),
        HumanMessage(content="latest-user", id="h-latest"),
    ]
    remaining = trim_channel_history(messages, max_tokens=10, token_counter=len)
    assert [item.id for item in remaining] == ["h-1", "a-1", "h-latest"]


def test_graph_entry_preserves_history_for_model_compaction():
    messages = [HumanMessage(content="x" * 100000, id="h-1")]
    assert make_prepare_context_node()({"messages": messages}, None) == {}
