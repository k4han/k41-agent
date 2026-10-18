---
# ============================================================================
# AGENT CARD - FULL FIELD REFERENCE / TEMPLATE
#
# This file documents every field an agent card can define. It is NOT loaded
# automatically; copy it into ~/.k41-agent/agents/<name>.md and remove any
# field you do not need.
#
# Format: YAML frontmatter (between the leading `---` markers) + Markdown body
# (the body becomes the agent's system prompt).
# ============================================================================

# --- Required ---------------------------------------------------------------
# Unique agent identifier. Letters, numbers, underscores and hyphens only.
# Must not have leading/trailing whitespace. A user card with the same name
# overrides a builtin card completely.
name: all-fields-agent

# --- Identity ---------------------------------------------------------------
# Human-readable label shown in the UI. Optional; falls back to `name`.
display_name: All Fields Agent

# Short summary of what the agent does. Optional.
description: Reference agent card that demonstrates every supported field.

# --- Workflow ---------------------------------------------------------------
# Workflow graph to run. Supported values:
#   react_agent    (default) standard tool-calling loop
#   router         routes a request to one of `sub_agents`
#   decision_agent pre-loads relevant workspace context before each turn
# Missing/empty defaults to `react_agent`.
graph_type: react_agent

# --- Model ------------------------------------------------------------------
# LLM provider name. Missing/null-like ("", none, null, ~) defaults to
# "default", which follows the runtime `llm.default_model` setting.
provider: default

# Model ID override. Missing/null/empty means "use the provider default model".
# Two equivalent shapes are accepted:
#   model: gemini-3.1-flash-lite          # flat string
#   model:                                # structured, carries the effort too
#     id: gemini-3.1-flash-lite
#     effort: high
model: ""

# Reasoning effort override for this agent's model.
# Pattern: ^[a-z][a-z0-9_-]{0,63}$  (e.g. low, medium, high, minimal)
# Use "auto" to fall back to the model's own default. Omit to inherit the
# agent's configured default. When using the structured `model` form above,
# set effort there instead - declaring both with different values is rejected.
reasoning_effort: high

# --- Tools ------------------------------------------------------------------
# Manual tool allow-list. An empty list (or a missing field, which defaults
# to []) disables manual tools. Callable sub-agents enable delegate_task separately.
# Names are canonicalized, so legacy
# aliases like read_file/write_file/edit_file map to read/write/edit.
tools:
  - ask_user
  - bash
  - edit
  - glob
  - grep
  - list_dir
  - read
  - read_process_output
  - skill
  - stop_process
  - web_fetch
  - web_search
  - write
  - write_process_input
  - write_todos

# Per-tool configuration overrides, keyed by canonical tool name. Only applied
# for tools that are also present in `tools`. Field names/values are validated
# against each tool's config schema (unknown fields are rejected).
# Secrets (password fields) accept a real value, "" or null to keep/reset.
tool_configs:
  web_search:
    provider: auto
    google_api_key: ""
    google_cse_id: ""
    tavily_api_key: ""
    firecrawl_base_url: ""
  web_fetch:
    provider: auto
    firecrawl_api_key: ""

# Per-agent permission rules for file/shell/external-directory operations.
#   null            -> inherit the global `tools.permissions` rules (default)
#   [] or a list    -> replace the global rules for this agent
# Each rule: action, resource, effect. Last matching rule wins.
#   effect: allow | ask | deny
#   action: read | edit | glob | grep | shell | external_directory |
#           read_process_output | write_process_input | stop_process
#   resource: glob matched against absolute file paths or the full shell command
#            (use "*" to match anything). Workspace reads/writes and shell are
#            allowed by default; explicit external paths require approval.
tool_permissions:
  - action: edit
    resource: "*/secrets/*"
    effect: deny
  - action: shell
    resource: "*"
    effect: ask

# --- Delegation -------------------------------------------------------------
# Sub-agents this agent may call through the `delegate_task` tool.
# The tool is enabled automatically when at least one target is callable.
#   (field omitted / null) -> leaf agent, cannot call anyone
#   []                     -> delegation is disabled
#   ["a", "b"]             -> may call only the listed agents
# Self-calls are blocked; targets are validated at runtime.
sub_agents:
  - coder
  - researcher

# Agents whose plan steps require human approval before execution. Targets must
# reference existing, non-hidden agent cards and cannot include this agent.
# Empty list = no plan approvals required.
plan_approval_targets:
  - reviewer

# --- Visibility -------------------------------------------------------------
# When true the card is hidden from the UI and cannot be used as a plan
# approval target. Optional; defaults to false.
hidden: false

# --- Context management -----------------------------------------------------
# Integer percentage (1-100, strict) of the model context window that triggers
# automatic compaction before a model call. Defaults to 75.
# NOTE: the legacy token settings `context_trim_threshold` / `max_context_tokens`
# are rejected - use this percentage field instead.
context_compact_threshold: 75
---

# System Prompt

Everything below the frontmatter is the agent's system prompt.

Supported runtime placeholders (filled automatically):
- `{{working_dir}}` - the active workspace directory
- `{{host_os}}` - host operating system name
- `{{shell_name}}` - active shell program
- `{{shell_kind}}` - shell syntax family (posix / powershell)
- `{{caller_agent_name}}` - the agent that invoked this one

Router-only required placeholders (when `graph_type: router`):
- `{agent_options}` - the candidate agents from `sub_agents`
- `{user_input}` - the incoming user request

You are a reference agent. Replace this section with the real instructions for
your agent: role, objectives, workflow, tool usage rules and output format.
