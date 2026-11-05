"""Constants for workflow graph types and context keys."""

# Graph type constants
ROUTER_GRAPH_TYPE = "router"
REACT_AGENT_GRAPH_TYPE = "react_agent"
DECISION_AGENT_GRAPH_TYPE = "decision_agent"
ACP_AGENT_GRAPH_TYPE = "acp_agent"

# Thread families: a thread's checkpoint stays on one side of the ACP boundary
GRAPH_FAMILY_ACP = "acp"
GRAPH_FAMILY_MODEL = "model"

# Context key constants
CONTEXT_KEY_AGENT_NAME = "agent_name"
CONTEXT_KEY_WORKING_DIR = "working_dir"

# Default values
DEFAULT_AGENT_NAME = "default"

# String normalization constants
STRIP_PREFIXES = "-* "
STRIP_QUOTES = "`'\""


__all__ = [
    "ROUTER_GRAPH_TYPE",
    "REACT_AGENT_GRAPH_TYPE",
    "DECISION_AGENT_GRAPH_TYPE",
    "ACP_AGENT_GRAPH_TYPE",
    "GRAPH_FAMILY_ACP",
    "GRAPH_FAMILY_MODEL",
    "CONTEXT_KEY_AGENT_NAME",
    "CONTEXT_KEY_WORKING_DIR",
    "DEFAULT_AGENT_NAME",
    "STRIP_PREFIXES",
    "STRIP_QUOTES",
]
