"""Retain the historical graph entry and export channel trimming."""

from agent.modules.workflows.history_trim import trim_channel_history


def make_prepare_context_node():
    """Keep the graph entry compatible with stored checkpoints.

    Context management now runs inside model nodes after resolving prompts and
    tools. Entry must not discard history before compaction.
    """
    def prepare_context_node(state, runtime):
        return {}

    return prepare_context_node
