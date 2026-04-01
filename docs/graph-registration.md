# Graph Registration Analysis

## Startup Flow

Khi khởi động, hệ thống gọi `register_builtin_workflows()` trong `agent/bootstrap/runtime.py:64`

## Graphs được build

```python
def register_builtin_workflows() -> None:
    build_react_graph()      # → Registers "react_agent"
    build_research_graph()   # → Registers "research_chain"
    build_router_graph()     # → Registers "router"
    scan_agents_from_md()    # → Loads agent configs (NOT graphs)
```

## Chi tiết

### 1. build_react_graph()
- **File:** `agent/modules/workflows/infrastructure/langgraph/graphs/react_agent.py`
- **Registers:** `"react_agent"` (graph_name parameter, default)
- **Type:** Shared template graph
- **Usage:** Được reuse bởi tất cả agents có `graph_type: "react_agent"`

### 2. build_research_graph()
- **File:** `agent/modules/workflows/infrastructure/langgraph/graphs/research.py`
- **Registers:** `"research_chain"`
- **Type:** Specialized workflow
- **Usage:** Research tasks với multi-step chain

### 3. build_router_graph()
- **File:** `agent/modules/workflows/infrastructure/langgraph/graphs/router.py`
- **Registers:** `"router"`
- **Type:** Routing workflow
- **Usage:** Route requests to appropriate handlers

### 4. scan_agents_from_md()
- **File:** `agent/modules/workflows/infrastructure/langgraph/graphs/agent_scan.py`
- **Action:** Load agent configs từ MD files vào catalog
- **Does NOT:** Build new graphs
- **Purpose:** Populate agent catalog với configs

## Tổng kết

**✓ Total compiled graphs: 3**
- react_agent (shared template)
- research_chain
- router

**✓ Agent configs: N** (loaded from `~/.kaka-agent/agents/*.md`)

**✓ Architecture:**
- Shared graph templates (3 graphs)
- Runtime config resolution (N agents)
- Agents reuse graph templates với custom config (model, system_prompt, tools)

## Ví dụ

Nếu có 10 agents trong MD files:
- Graphs compiled: **3** (react_agent, research_chain, router)
- Agent configs loaded: **10 + 1 builtin default = 11**
- Memory efficient: Không build 11 graphs riêng biệt

## Lợi ích

1. **Memory efficient:** Chỉ 3 compiled graphs thay vì N graphs
2. **Fast startup:** Không cần compile graph cho mỗi agent
3. **Dynamic config:** Agent config resolved at runtime
4. **Scalable:** Thêm agents không tăng số graphs
