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
max_context_tokens: 50000
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
- **max_context_tokens**: Token budget cho context trimming
- **system_prompt**: Nội dung sau frontmatter, là system prompt của agent

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
