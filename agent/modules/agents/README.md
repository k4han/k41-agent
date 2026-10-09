# Agent System

Agent system cho phép định nghĩa các AI agents thông qua Markdown files với YAML frontmatter.

## Kiến trúc

```
User Request (agent_name)
    ↓
API Router (router.py)
    ↓
build_run_params: agent_name → AgentConfig → workflow + config
    ↓
run_agent: execute workflow với agent context
    ↓
llm_node: load AgentConfig → resolve model, system_prompt, tools
    ↓
LLM Response
```

## Agent File Format

Agent được định nghĩa trong file `.md` với cấu trúc:

```markdown
---
name: "agent-name"
description: "Agent description"
graph_type: "react_agent"  # workflow template to use
provider: "default"  # required; "default" follows llm.default_provider
model: ""  # optional override; empty = use provider default model
tools:
  - "tool1"
  - "tool2"
sub_agents:  # Optional: list of agents this agent can call
  - "sub-agent-1"
  - "sub-agent-2"
context_compact_threshold: 75
---

# System Prompt

Your agent's system prompt goes here.
Can use {working_dir} placeholder.
```

## Fields

- **name** (required): Unique identifier cho agent
- **description**: Mô tả ngắn gọn về agent
- **graph_type**: Workflow template (default: `react_agent`)
- **provider**: Provider name. Use `default` to follow `llm.default_provider`
- **model**: Model ID override (default: empty, so runtime uses provider default)
- **tools**: Danh sách tools agent có thể sử dụng (empty = all default tools)
- **sub_agents**: 
  - `null` (không có field): leaf agent, không thể call sub-agents
  - `[]` (empty list): có thể call sub-agents nhưng chưa config
  - `["agent1", "agent2"]`: chỉ có thể call các agents trong list
- **context_compact_threshold**: Integer percentage (1-100) of the model context window that triggers automatic compaction before each model call; default 75.
- **system_prompt**: Nội dung sau frontmatter, là system prompt của agent

## Context settings migration

Agent cards containing `context_trim_threshold` or `max_context_tokens` are rejected.
Remove those token settings and use `context_compact_threshold: 75` (or another integer
from 1 to 100). Cards without a threshold default to 75 percent.

Channel trimming is configured independently in dashboard channel settings:
`channels.telegram.context_trim_threshold`, `channels.discord.context_trim_threshold`,
and `channels.zalo.context_trim_threshold` each default to 50,000 tokens. At the start
of a channel turn, compaction runs before trimming. Sub-agent threads and background
tasks compact independently and do not inherit channel trimming.

GitHub repository bindings use an optional `context_compact_threshold` percentage;
null inherits the executing agent card. Existing token values are not converted.

Context limits come from the provider/model catalog, then model metadata, with a
128,000-token fallback. Input estimates include the rendered prompt and tool schemas.
Compaction must reduce the normalized history and fit the input budget, including
the summary and its wrapper messages. The budget reserves response capacity (the
model's configured output limit, or up to 4,096 tokens by default) and estimation
headroom (2 percent, capped at 2,048 tokens). At high compaction thresholds, this
safety ceiling can trigger compaction before the configured percentage is reached.
Failed compaction preserves history; execution continues only when the remaining
input fits the safety ceiling. Otherwise the model call is stopped with an
actionable context budget error.

Manual compaction uses the thread's agent threshold and the latest available
system/tool/skill prompt estimates. Those estimates are not a freshly rendered
prompt. Both manual and automatic compaction enforce the same history budget,
with up to three summary generations and no partial tool groups.

Summaries receive tool arguments, result statuses, error diagnostics, attachment
references, and retained output paths. Oversized tool text keeps its beginning,
ending, and selected diagnostics rather than only a short leading preview.

Manual compaction reserves the thread in the active session registry, excluding
new runs and competing checkpoint mutations in the same application process.
Background notifications wait for this reservation and are then appended. The
checkpoint and pending writes are checked again before an update pinned to the
original checkpoint; the response is loaded from the resulting checkpoint.
Reservations are released on success, failure, and cancellation. This is not a
distributed lock across application processes sharing a database.

## Agent Discovery

Agents được load theo thứ tự ưu tiên (sau ghi đè trước):

1. **Builtin agents** — `agent/modules/agents/infrastructure/_builtin/*.md` (bundled cùng package)
2. **User agents** — `~/.k41-agent/agents/*.md` (primary)

**Override rule**: Nếu user tạo agent có cùng `name` với builtin (ví dụ `name: "default"`),
phiên bản của user sẽ được ưu tiên và ghi đè hoàn toàn builtin. Log sẽ ghi:
```
INFO  User agent 'default' (/path/to/default.md) overrides builtin.
```

## Usage

### API Request

```python
# Request với agent_name
{
    "message": "Hello",
    "agent_name": "research-agent",
    "user_id": "user123"
}
```

### Programmatic

```python
from agent.modules.agents import get_catalog_service

catalog = get_catalog_service()

# Get agent config
config = catalog.get_agent("research-agent")

# List all agents
agents = catalog.list_agents()

# Check sub-agent permissions
callable = catalog.get_callable_agents("parent-agent")
is_allowed = catalog.validate_call("parent", "child")

# Reload from filesystem
catalog.reload_agents()
```

## Sub-Agent Hierarchy

Agents có thể gọi sub-agents thông qua `call_agent` tool:

```markdown
---
name: "orchestrator"
tools:
  - "call_agent"
sub_agents:
  - "researcher"
  - "coder"
---

You can delegate tasks to researcher or coder agents.
```

Rules:
- `sub_agents: null` → leaf agent, không thể call ai
- `sub_agents: []` → có thể call nhưng chưa config
- `sub_agents: ["a", "b"]` → chỉ call được a và b
- Self-calls bị block
- Validation xảy ra tại runtime

### Router opt-in via card

Để bật router theo card, tạo một agent orchestrator với:
- `graph_type: "router"`
- `sub_agents` chứa danh sách agent mục tiêu được phép route đến
- `system_prompt` có placeholder bắt buộc: `{agent_options}` và `{user_input}`

Router sẽ chỉ chọn trong danh sách `sub_agents` của orchestrator đó.

Ví dụ router system prompt:

```text
You are a routing orchestrator.
Candidates:
{agent_options}

User request:
{user_input}

Return only the selected agent name.
```

## Examples

Xem `agent/modules/agents/examples/` cho các agent mẫu:
- `default-agent.md`: General-purpose assistant
- `research-agent.md`: Research specialist
- `backend-agent.md`: Python/backend engineer
- `frontend-agent.md`: React/TypeScript engineer
- `devops-agent.md`: DevOps engineer
- `router-orchestrator-agent.md`: Router orchestrator (opt-in with `graph_type: router`)

## Module Structure

```
agent/modules/agents/
├── domain/
│   └── subagent.py                # AgentConfig model
├── infrastructure/
│   ├── _builtin/
│   │   └── default.md             # Bundled default agent (loaded first)
│   ├── parser.py                  # MD file parser
│   └── repository.py              # Filesystem scanning & caching
├── application/
│   └── service.py                 # Business logic & validation
├── examples/                      # Sample agent definitions (không load tự động)
└── public.py                      # Public API
```
