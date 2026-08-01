# Plan: Tích hợp ACP Agents (Claude Code, Gemini CLI, OpenCode)

## Tổng quan

Thêm `graph_type: acp_agent` vào hệ thống agent cards, cho phép sử dụng Claude Code CLI, Gemini CLI, hoặc OpenCode CLI như các workflow graph riêng biệt, tương tác qua ACP protocol (Agent Client Protocol - JSON-RPC 2.0 over stdio).

**Trạng thái**: Draft  
**Ngày tạo**: 2026-07-14  
**Ưu tiên**: Local process trước, sandbox (Daytona/Modal) sau

---

## Mục tiêu

1. Hỗ trợ 3 ACP agents: Claude Code, Gemini CLI, OpenCode
2. ACP agents hoạt động như graph type mới (`acp_agent`) trong agent cards
3. Streaming real-time từ ACP agents về UI
4. Xử lý permission requests từ ACP agents
5. Tương thích với workspace backends hiện có

---

## Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────┐
│                   Agent Card (Markdown)              │
│  graph_type: acp_agent                              │
│  acp_agent_type: claude_code | gemini_cli | opencode│
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Runner (runner.py)                      │
│  Check graph_type == "acp_agent"                    │
│  → Route to ACP Workflow Graph                      │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│           ACP Workflow Graph (acp_agent.py)          │
│  LangGraph-compatible, spawn ACP agent process      │
│  và communicate via stdio                           │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│            ACP Client Module (acp_client/)           │
│  - Process spawn & lifecycle management             │
│  - ACP Protocol handshake (initialize, session/new) │
│  - Streaming (session/update notifications)         │
│  - Permission handling (session/request_permission) │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│         ACP Agent Processes (stdio)                  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │
│  │claude-code  │ │gemini --acp │ │opencode acp │   │
│  │    -acp     │ │             │ │             │   │
│  └─────────────┘ └─────────────┘ └─────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## Chi tiết các thành phần

### 1. Mở rộng AgentConfig model

**File**: `agent/modules/agents/models.py`

```python
class AgentConfig(BaseModel):
    # ... existing fields ...
    
    # ACP-specific fields (chỉ dùng khi graph_type == "acp_agent")
    acp_agent_type: Optional[Literal["claude_code", "gemini_cli", "opencode"]] = None
    acp_command: Optional[str] = None  # Custom command override
    acp_args: list[str] = Field(default_factory=list)
    acp_env: dict[str, str] = Field(default_factory=dict)
    acp_sandbox: bool = False  # Future: run in Daytona/Modal
```

### 2. Agent Card examples

**Claude Code** (`_builtin/claude-code.md`):

```markdown
---
name: claude-code
display_name: Claude Code
description: Anthropic's Claude Code CLI via ACP
graph_type: acp_agent
provider: Anthropic
model: claude-sonnet-4-20250514
acp_agent_type: claude_code
tools: []
context_trim_threshold: 200000
---

# System Prompt

You are Claude Code, an AI coding assistant by Anthropic.
```

**Gemini CLI** (`_builtin/gemini-cli.md`):

```markdown
---
name: gemini-cli
display_name: Gemini CLI
description: Google's Gemini CLI via ACP
graph_type: acp_agent
provider: Google
model: gemini-2.5-pro
acp_agent_type: gemini_cli
tools: []
context_trim_threshold: 1000000
---

# System Prompt

You are Gemini CLI, an AI coding assistant by Google.
```

**OpenCode** (`_builtin/opencode.md`):

```markdown
---
name: opencode
display_name: OpenCode
description: OpenCode CLI via ACP
graph_type: acp_agent
provider: OpenAI
model: gpt-4o
acp_agent_type: opencode
tools: []
context_trim_threshold: 128000
---

# System Prompt

You are OpenCode, an AI coding assistant.
```

### 3. ACP Client Module

**Cấu trúc**:

```
agent/modules/acp/
├── __init__.py
├── client.py          # ACP protocol client (JSON-RPC over stdio)
├── protocol.py        # ACP protocol types & messages
├── process.py         # Process spawning & lifecycle
├── stream.py          # Streaming handler for session/update
└── agents/
    ├── __init__.py
    ├── claude_code.py  # Claude Code specific config
    ├── gemini_cli.py   # Gemini CLI specific config
    └── opencode.py     # OpenCode specific config
```

**`protocol.py`** - ACP Protocol Types:

```python
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel

class ACPMessageType(str, Enum):
    INITIALIZE = "initialize"
    SESSION_NEW = "session/new"
    SESSION_PROMPT = "session/prompt"
    SESSION_UPDATE = "session/update"
    SESSION_CANCEL = "session/cancel"
    SESSION_REQUEST_PERMISSION = "session/request_permission"

class ACPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int
    method: str
    params: dict[str, Any]

class ACPResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None

class ACPNotification(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any]

class AgentCapabilities(BaseModel):
    load_session: bool = False
    prompt_capabilities: dict[str, bool] = {}
    mcp_capabilities: dict[str, bool] = {}
    session_capabilities: dict[str, Any] = {}

class SessionUpdate(BaseModel):
    session_update: str
    # ... other fields based on update type
```

**`client.py`** - Core ACP Client:

```python
import asyncio
import json
from typing import AsyncGenerator, Any

class ACPClient:
    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._update_queue: asyncio.Queue[SessionUpdate] = asyncio.Queue()
        
    async def initialize(self) -> AgentCapabilities:
        """Initialize ACP connection and negotiate capabilities."""
        response = await self._send_request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": True, "writeTextFile": True},
                "terminal": True,
            },
            "clientInfo": {
                "name": "kaka-agent",
                "title": "Kaka Agent",
                "version": "1.0.0",
            },
        })
        return AgentCapabilities(**response["result"]["agentCapabilities"])
    
    async def new_session(
        self, 
        cwd: str, 
        mcp_servers: list[dict] = [],
    ) -> str:
        """Create a new ACP session."""
        response = await self._send_request("session/new", {
            "cwd": cwd,
            "mcpServers": mcp_servers,
        })
        return response["result"]["sessionId"]
    
    async def prompt(
        self, 
        session_id: str, 
        prompt: list[dict],
    ) -> None:
        """Send a prompt to the ACP agent."""
        await self._send_request("session/prompt", {
            "sessionId": session_id,
            "prompt": prompt,
        })
    
    async def cancel(self, session_id: str) -> None:
        """Cancel an ongoing prompt."""
        await self._send_notification("session/cancel", {
            "sessionId": session_id,
        })
    
    async def stream_updates(
        self, 
        session_id: str,
    ) -> AsyncGenerator[SessionUpdate, None]:
        """Stream session updates from the ACP agent."""
        while True:
            update = await self._update_queue.get()
            if update is None:  # Sentinel for stream end
                break
            yield update
    
    async def _send_request(
        self, 
        method: str, 
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        self._request_id += 1
        request_id = self._request_id
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        # Send via stdin
        line = json.dumps(request) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()
        
        # Wait for response
        return await asyncio.wait_for(future, timeout=300)
    
    async def _send_notification(
        self, 
        method: str, 
        params: dict[str, Any],
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(notification) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()
    
    async def _read_loop(self):
        """Read and dispatch messages from stdout."""
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            
            try:
                message = json.loads(line.decode())
            except json.JSONDecodeError:
                continue
            
            # Dispatch based on message type
            if "id" in message:
                # Response to a request
                request_id = message["id"]
                future = self._pending_requests.pop(request_id, None)
                if future:
                    if "error" in message:
                        future.set_exception(
                            ACPError(message["error"])
                        )
                    else:
                        future.set_result(message)
            elif message.get("method") == "session/update":
                # Streaming update
                update = SessionUpdate(**message["params"]["update"])
                await self._update_queue.put(update)
            elif message.get("method") == "session/request_permission":
                # Permission request - handle or queue
                await self._handle_permission_request(message)
    
    async def _handle_permission_request(self, message: dict):
        """Handle permission request from ACP agent."""
        # Default: auto-approve all
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "outcome": {
                    "outcome": "selected",
                    "optionId": "allow-once",
                }
            },
        }
        line = json.dumps(response) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()
```

**`process.py`** - Process Management:

```python
import asyncio
import sys
from pathlib import Path

class ACPProcessManager:
    """Manages ACP agent processes."""
    
    # Default commands for each agent type
    AGENT_COMMANDS = {
        "claude_code": {
            "command": "claude-code-acp",
            "args": [],
            "env_key": "ANTHROPIC_API_KEY",
        },
        "gemini_cli": {
            "command": "gemini",
            "args": ["--experimental-acp"],
            "env_key": "GEMINI_API_KEY",
        },
        "opencode": {
            "command": "opencode",
            "args": ["acp"],
            "env_key": "OPENAI_API_KEY",
        },
    }
    
    async def spawn(
        self,
        agent_type: str,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[asyncio.subprocess.Process, ACPClient]:
        """Spawn an ACP agent process and return client."""
        config = self.AGENT_COMMANDS.get(agent_type, {})
        
        final_command = command or config.get("command", agent_type)
        final_args = args or config.get("args", [])
        
        # Build environment
        process_env = {**env} if env else {}
        
        # Spawn process
        process = await asyncio.create_subprocess_exec(
            final_command,
            *final_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
        )
        
        client = ACPClient(process)
        return process, client
    
    async def kill(self, process: asyncio.subprocess.Process) -> None:
        """Kill an ACP agent process."""
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
    
    async def health_check(self, process: asyncio.subprocess.Process) -> bool:
        """Check if process is still running."""
        return process.returncode is None
```

**`agents/claude_code.py`** - Claude Code Config:

```python
"""Claude Code ACP agent configuration."""

AGENT_TYPE = "claude_code"

DEFAULT_COMMAND = "claude-code-acp"
DEFAULT_ARGS = []

# Claude Code uses ANTHROPIC_API_KEY
REQUIRED_ENV_VARS = ["ANTHROPIC_API_KEY"]

# Claude Code capabilities
CAPABILITIES = {
    "load_session": True,
    "prompt_capabilities": {
        "image": True,
        "audio": False,
        "embedded_context": True,
    },
    "session_capabilities": {
        "resume": True,
        "close": True,
        "delete": True,
    },
}

# Known models
MODELS = [
    "claude-sonnet-4-20250514",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
]
```

### 4. ACP Workflow Graph

**File**: `agent/modules/workflows/graphs/acp_agent.py`

```python
"""ACP agent graph template — spawns ACP agent and communicates via stdio."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.modules.workflows.checkpoint import get_checkpointer
from agent.modules.workflows.registry import GraphRegistry
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.base import BaseState
from agent.modules.acp.client import ACPClient
from agent.modules.acp.process import ACPProcessManager


async def spawn_acp_agent_node(state: BaseState, context: WorkflowContext) -> dict:
    """Spawn ACP agent process."""
    agent_config = context.agent_config
    
    if not agent_config.acp_agent_type:
        raise ValueError("acp_agent_type is required for acp_agent graph_type")
    
    manager = ACPProcessManager()
    process, client = await manager.spawn(
        agent_type=agent_config.acp_agent_type,
        command=agent_config.acp_command,
        args=agent_config.acp_args,
        env=agent_config.acp_env,
    )
    
    # Store in state for other nodes
    return {
        "acp_process": process,
        "acp_client": client,
        "acp_capabilities": None,
        "acp_session_id": None,
    }


async def initialize_acp_node(state: BaseState, context: WorkflowContext) -> dict:
    """Initialize ACP connection."""
    client: ACPClient = state["acp_client"]
    
    capabilities = await client.initialize()
    
    # Create session
    working_dir = context.working_dir or "."
    session_id = await client.new_session(cwd=working_dir)
    
    return {
        "acp_capabilities": capabilities,
        "acp_session_id": session_id,
    }


async def send_prompt_node(state: BaseState, context: WorkflowContext) -> dict:
    """Send user prompt to ACP agent."""
    client: ACPClient = state["acp_client"]
    session_id: str = state["acp_session_id"]
    
    # Get user message from state
    messages = state.get("messages", [])
    user_message = messages[-1].content if messages else ""
    
    # Build ACP prompt
    prompt = [{"type": "text", "text": user_message}]
    
    # Send prompt (non-blocking, streaming happens in handle_streaming)
    asyncio.create_task(client.prompt(session_id, prompt))
    
    return {}


async def handle_streaming_node(state: BaseState, context: WorkflowContext) -> dict:
    """Handle streaming updates from ACP agent."""
    client: ACPClient = state["acp_client"]
    session_id: str = state["acp_session_id"]
    
    accumulated_text = ""
    
    async for update in client.stream_updates(session_id):
        if update.session_update == "agent_message_chunk":
            # Extract text delta
            content = update.get("content", {})
            if content.get("type") == "text":
                accumulated_text += content["text"]
        
        elif update.session_update == "tool_call":
            # Tool call started
            pass
        
        elif update.session_update == "tool_call_update":
            # Tool call progress
            pass
        
        elif update.session_update == "usage_update":
            # Usage stats
            pass
    
    # Return final response
    return {
        "messages": [
            {"role": "assistant", "content": accumulated_text}
        ],
    }


def build_acp_graph(
    checkpointer=None,
    *,
    graph_name: str = "acp_agent",
) -> None:
    """Build ACP agent graph template."""
    
    if checkpointer is None:
        checkpointer = get_checkpointer()
    
    graph = StateGraph(BaseState, context_schema=WorkflowContext)
    
    # Add nodes
    graph.add_node("spawn_acp_agent", spawn_acp_agent_node)
    graph.add_node("initialize_acp", initialize_acp_node)
    graph.add_node("send_prompt", send_prompt_node)
    graph.add_node("handle_streaming", handle_streaming_node)
    
    # Add edges
    graph.add_edge(START, "spawn_acp_agent")
    graph.add_edge("spawn_acp_agent", "initialize_acp")
    graph.add_edge("initialize_acp", "send_prompt")
    graph.add_edge("send_prompt", "handle_streaming")
    graph.add_edge("handle_streaming", END)
    
    # Register
    GraphRegistry.register(
        graph_name,
        graph.compile(checkpointer=checkpointer),
        description="ACP agent (Claude Code, Gemini CLI, OpenCode)",
    )


__all__ = ["build_acp_graph"]
```

### 5. Runner Integration

**File**: `agent/modules/agent_runtime/runner.py`

Thêm routing cho ACP agents:

```python
# Trong run_agent() và run_agent_stream(), sau khi resolve workflow:

if resolved_workflow == "acp_agent":
    from agent.modules.acp.workflow import run_acp_agent_stream
    async for event in run_acp_agent_stream(
        user_input=user_input,
        thread_id=thread_id,
        agent_config=agent_config,
        workspace=workspace,
    ):
        yield event
    return
```

### 6. Parser Updates

**File**: `agent/modules/agents/parser.py`

```python
# Trong _build_agent_config(), thêm parsing cho ACP fields:

acp_agent_type = data.get("acp_agent_type")
if acp_agent_type and graph_type != "acp_agent":
    raise AgentMarkdownError(
        f"Agent file {source_label} has acp_agent_type but graph_type is not 'acp_agent'."
    )

acp_command = data.get("acp_command")
acp_args = parse_string_or_list(data.get("acp_args", []))
acp_env = data.get("acp_env", {}) or {}
acp_sandbox = bool(data.get("acp_sandbox", False))

return AgentConfig(
    # ... existing fields ...
    acp_agent_type=acp_agent_type,
    acp_command=acp_command,
    acp_args=acp_args,
    acp_env=acp_env,
    acp_sandbox=acp_sandbox,
)
```

### 7. Constants Update

**File**: `agent/modules/workflows/constants.py`

```python
# Thêm constant mới
ACP_AGENT_GRAPH_TYPE = "acp_agent"
```

### 8. Register New Graph

**File**: `agent/modules/workflows/register_builtin_workflows.py`

```python
from agent.modules.workflows.graphs import (
    build_react_graph,
    build_research_graph,
    build_router_graph,
    build_acp_graph,  # Thêm import
)

def register_builtin_workflows() -> None:
    build_react_graph()
    build_research_graph()
    build_router_graph()
    build_acp_graph()  # Thêm registration
    scan_agents_from_md()
```

### 9. Dependencies

**File**: `pyproject.toml`

```toml
[project]
dependencies = [
    # ... existing ...
    "agent-client-protocol>=0.11.0",
]
```

---

## Touchpoints kiểm tra

| Touchpoint | File | Cần sửa | Ghi chú |
|------------|------|---------|---------|
| AgentConfig model | `agent/modules/agents/models.py` | ✅ | Thêm ACP fields |
| Agent Parser | `agent/modules/agents/parser.py` | ✅ | Parse ACP fields |
| Agent Cards | `agent/modules/agents/_builtin/*.md` | ✅ | Tạo 3 cards mới |
| Graph Registry | `agent/modules/workflows/registry.py` | ❌ | Không cần sửa |
| Graph Constants | `agent/modules/workflows/constants.py` | ✅ | Thêm `acp_agent` |
| Register Graphs | `agent/modules/workflows/register_builtin_workflows.py` | ✅ | Register `acp_agent` |
| ACP Workflow Graph | `agent/modules/workflows/graphs/acp_agent.py` | ✅ | Tạo mới |
| ACP Client Module | `agent/modules/acp/*` | ✅ | Tạo mới |
| Runner | `agent/modules/agent_runtime/runner.py` | ✅ | Route ACP agents |
| Workspace | `agent/modules/workspaces/` | ⚠️ | Phase 2 |
| Tool System | `agent/modules/tools/` | ❌ | ACP agents có tools riêng |
| MCP Integration | `agent/modules/mcp/` | ⚠️ | Có thể cần |
| CLI REPL | `agent/delivery/cli/repl.py` | ⚠️ | Hiển thị ACP output |
| Dashboard | `agent/delivery/http/dashboard/` | ⚠️ | Hiển thị status |

---

## Timeline

### Phase 1: Foundation (1-2 ngày)
- [ ] Mở rộng AgentConfig model
- [ ] Cập nhật Agent Parser
- [ ] Tạo 3 agent cards mới
- [ ] Thêm constants

### Phase 2: ACP Client (2-3 ngày)
- [ ] Implement `protocol.py`
- [ ] Implement `process.py`
- [ ] Implement `client.py`
- [ ] Implement agent configs (`claude_code.py`, `gemini_cli.py`, `opencode.py`)

### Phase 3: Workflow Graph (2-3 ngày)
- [ ] Implement `acp_agent.py` graph
- [ ] Register graph
- [ ] Integrate with Runner

### Phase 4: Streaming & Permissions (1-2 ngày)
- [ ] Implement streaming handler
- [ ] Implement permission handling
- [ ] Test với actual ACP agents

### Phase 5: UI Integration (1-2 ngày)
- [ ] Cập nhật CLI REPL
- [ ] Cập nhật Dashboard
- [ ] Test end-to-end

### Phase 6: Sandbox (Future)
- [ ] Tích hợp với Daytona backend
- [ ] Tích hợp với Modal backend
- [ ] Test sandbox execution

---

## Rủi ro & Mitigations

| Rủi ro | Impact | Mitigation |
|--------|--------|------------|
| ACP agent process crash | High | Health check + auto-restart |
| Streaming latency | Medium | Async processing, buffer |
| Permission prompt blocking | High | Auto-approve mode hoặc background handling |
| Windows `.cmd` shim issues | Medium | Process spawning wrapper |
| ACP protocol version mismatch | Low | Version negotiation trong initialize |
| ACP agent not installed | Medium | Clear error message + installation guide |
| API key not configured | Medium | Validate env vars trước khi spawn |

---

## Testing Strategy

1. **Unit tests**: ACP client, protocol, process management
2. **Integration tests**: Spawn actual ACP agents (mock hoặc real)
3. **E2E tests**: Full flow từ agent card đến response
4. **Manual tests**: Test với Claude Code, Gemini CLI, OpenCode thực tế

---

## Notes

- ACP Python SDK (`agent-client-protocol`) chỉ cung cấp protocol-level, không có runtime-level như ACP Kit (TypeScript)
- Cần implement process management và session handling thủ công
- ACP agents có tools riêng, không cần tích hợp với tool system hiện có
- Có thể mở rộng sang MCP integration trong tương lai
