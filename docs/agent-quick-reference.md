# Agent System - Quick Reference

## Sử dụng Agent qua API

### Request với agent_name

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Help me research about LangGraph",
    "agent_name": "research",
    "user_id": "user123"
  }'
```

### Request với workflow (legacy)

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello",
    "workflow": "react_agent",
    "user_id": "user123"
  }'
```

## Tạo Agent Mới

### 1. Tạo file MD trong ~/.kaka-agent/agents/

```bash
nano ~/.kaka-agent/agents/my-agent.md
```

### 2. Định nghĩa agent config

```markdown
---
name: "my-agent"
description: "My custom agent"
graph_type: "react_agent"
model: "devstral-2512"
tools:
  - "list_files"
  - "read_file"
max_context_tokens: 50000
---

# System Prompt

You are my custom agent.
Working directory: {working_dir}
```

### 3. Restart service để load agent

```bash
# Service sẽ tự động load agents từ ~/.kaka-agent/agents/
```

## Agent với Sub-agents

### Parent agent

```markdown
---
name: "orchestrator"
tools:
  - "call_agent"
sub_agents:
  - "research"
  - "backend"
---

You can delegate tasks to research or backend agents.
```

### Sử dụng call_agent tool

LLM sẽ tự động gọi:
```json
{
  "tool": "call_agent",
  "args": {
    "task": "Research about Python async",
    "sub_agent": "research"
  }
}
```

## Available Tools

### File Operations
- `list_files` - List directory contents
- `read_file` - Read file
- `write_file` - Write to file
- `search_files` - Search in files

### Web
- `websearch` - Search the web
- `webfetch` - Fetch web content

### Shell (Session-based)
- `bash` - Execute shell commands in a persistent terminal session
- `bash_send_input` - Send input to an interactive running process
- `bash_interrupt` - Interrupt or terminate a running process
- `bash_read_output` - Read output from a background process
- `bash_list_sessions` - List all active terminal sessions
- `bash_close` - Close active terminal sessions

> **Session behavior:** `bash` tools maintain state across calls (same `session_id`). `cd`, environment variables, and background processes persist between invocations. Use different `session_id` values to fully isolate tasks.

### Agent
- `call_agent` - Call sub-agents

## Graph Types

### react_agent (default)
- ReAct pattern: Reasoning + Acting
- Tool calling loop
- Shared template cho nhiều agents

### research_chain
- Multi-step research workflow
- Research → Summarize

### router
- Route requests to handlers

## Models

### devstral-2512 (default)
- Fast, efficient
- Good for general tasks

### claude-sonnet-4-5-20250929
- More capable
- Better for complex reasoning
- Use for research, analysis

## Debugging

### Check loaded agents

```python
from agent.modules.agents import get_catalog_service

catalog = get_catalog_service()
agents = catalog.list_agents()
for agent in agents:
    print(f"{agent.name}: {agent.description}")
```

### Validate sub-agent calls

```python
catalog = get_catalog_service()
is_allowed = catalog.validate_call("parent", "child")
print(f"Can parent call child? {is_allowed}")
```

### List registered graphs

```python
from agent.modules.workflows import list_registered_workflows

graphs = list_registered_workflows()
print(f"Available graphs: {graphs}")
```

## Common Patterns

### General assistant
```yaml
name: "assistant"
graph_type: "react_agent"
tools: ["list_files", "read_file", "write_file"]
```

### Research specialist
```yaml
name: "researcher"
graph_type: "react_agent"
model: "claude-sonnet-4-5-20250929"
tools: ["websearch", "webfetch"]
```

### Code assistant
```yaml
name: "coder"
graph_type: "react_agent"
tools: ["list_files", "read_file", "write_file", "run_command"]
```

### Orchestrator
```yaml
name: "orchestrator"
tools: ["call_agent"]
sub_agents: ["researcher", "coder"]
```

## Troubleshooting

### Agent not found
- Check file exists in ~/.kaka-agent/agents/
- Verify YAML frontmatter is valid
- Check name field matches filename (optional but recommended)

### Tool not available
- Check tool name spelling
- Verify tool is registered in registry
- Empty tools list = all default tools

### Sub-agent call denied
- Check sub_agents list includes target
- Verify target agent exists
- Check for circular dependencies

## Best Practices

1. **Naming**: Use kebab-case for agent names
2. **Description**: Clear, concise description
3. **Tools**: Only include needed tools
4. **System Prompt**: Specific instructions, use {working_dir}
5. **Model**: Choose appropriate model for task
6. **Sub-agents**: Keep hierarchy shallow (max 2-3 levels)

## Performance

- **Startup**: 3 graphs compiled (fast)
- **Runtime**: Config resolved per request (minimal overhead)
- **Memory**: O(1) graphs for O(N) agents
- **Scalability**: Add unlimited agents without performance impact

## Documentation

- Full docs: `agent/modules/agents/README.md`
- Graph registration: `docs/graph-registration.md`
- Examples: `agent/modules/agents/examples/`
