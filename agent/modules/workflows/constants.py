"""Constants for workflow graph types and context keys."""

# Graph type constants
ROUTER_GRAPH_TYPE = "router"
REACT_AGENT_GRAPH_TYPE = "react_agent"

# Context key constants
CONTEXT_KEY_AGENT_NAME = "agent_name"
CONTEXT_KEY_WORKING_DIR = "working_dir"

# Default values
DEFAULT_AGENT_NAME = "default"

# String normalization constants
STRIP_PREFIXES = "-* "
STRIP_QUOTES = "`'\""
NAME_SEPARATORS = ("_", "-")


__all__ = [
    "ROUTER_GRAPH_TYPE",
    "REACT_AGENT_GRAPH_TYPE",
    "CONTEXT_KEY_AGENT_NAME",
    "CONTEXT_KEY_WORKING_DIR",
    "DEFAULT_AGENT_NAME",
    "STRIP_PREFIXES",
    "STRIP_QUOTES",
    "NAME_SEPARATORS",
]
