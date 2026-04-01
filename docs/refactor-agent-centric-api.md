# Refactor: Agent-Centric API

## Tổng quan

Refactor các hàm `run_agent`, `run_agent_stream`, `run_agent_full` để sử dụng agent-centric approach - tất cả config được load từ `agent_name`, với khả năng override khi cần.

## Thay đổi chính

### Trước đây

```python
async def run_agent(
    workflow: str,
    user_input: str,
    thread_id: str,
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = 50_000,
    agent_name: str = "default",
) -> AsyncGenerator[str, None]:
    # Load agent config chỉ để lấy tools
    # Các tham số khác được truyền trực tiếp
```

**Vấn đề:**
- Duplicate logic: Load agent config ở nhiều nơi
- Inconsistency: Tham số truyền vào có thể không khớp với agent config
- Partial loading: Chỉ lấy `tools` từ config, các field khác vẫn truyền riêng
- Verbose API: Nhiều tham số, dễ nhầm lẫn

### Sau khi refactor

```python
async def run_agent(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    service_type: str | None = None,
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    allowed_tool_names: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    # Load toàn bộ config từ agent_name
    # Cho phép override từng field khi cần
```

**Lợi ích:**
- ✅ Single source of truth: Tất cả config từ agent definition
- ✅ Consistency: Đảm bảo tất cả tham số đồng bộ với agent config
- ✅ Simpler API: Agent-first, ít tham số bắt buộc
- ✅ Centralized logic: Resolve config ở một chỗ
- ✅ Vẫn linh hoạt: Có thể override khi cần (testing, debugging)

## Chi tiết thay đổi

### 1. `run_agent`, `run_agent_stream`, `run_agent_full`

**Signature mới:**
- `user_input`, `thread_id`, `agent_name` là positional/required
- `workflow`, `service_type`, `working_dir`, `max_context_tokens`, `allowed_tool_names` là keyword-only và optional
- Tất cả optional params đều có giá trị `None`, được resolve từ agent config

**Resolution logic:**
```python
catalog = get_catalog_service()
agent_config = catalog.get_agent(agent_name)

# Explicit params > agent config
resolved_workflow = workflow or agent_config.graph_type
resolved_service_type = service_type or agent_config.service_type
resolved_max_tokens = max_context_tokens or agent_config.max_context_tokens
resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools
```

### 2. `build_run_params`

**Thay đổi:**
- Không còn resolve workflow/service_type/max_context_tokens
- Chỉ build dict params để truyền vào `run_agent*` functions
- Resolution logic được chuyển vào các `run_agent*` functions

**Lý do:**
- Tách biệt concerns: `build_run_params` chỉ build params, không resolve
- Tránh duplicate logic: Resolution chỉ ở một chỗ (trong `run_agent*`)

### 3. Router và Handlers

**Không thay đổi nhiều:**
- Vẫn sử dụng `build_run_params` và `**params`
- Tự động tương thích với signature mới

## Migration guide

### Nếu bạn đang gọi trực tiếp `run_agent*`:

**Trước:**
```python
await run_agent(
    workflow="react_agent",
    user_input="hello",
    thread_id="123",
    service_type="backend",
    agent_name="default",
)
```

**Sau:**
```python
# Cách 1: Để agent config quyết định tất cả
await run_agent(
    user_input="hello",
    thread_id="123",
    agent_name="default",
)

# Cách 2: Override một số field
await run_agent(
    user_input="hello",
    thread_id="123",
    agent_name="default",
    workflow="custom_workflow",  # override
    service_type="backend",      # override
)
```

### Nếu bạn đang dùng `build_run_params`:

**Không cần thay đổi** - vẫn hoạt động như cũ vì `run_agent*` tự động resolve.

## Testing

Tất cả tests đã được cập nhật và pass:
- `tests/test_api_router.py` - 3 tests ✅
- `tests/test_default_agent_tools.py` - 4 tests ✅
- `tests/test_default_agent_bugfix.py` - 2 tests ✅

## Files thay đổi

- `agent/modules/agent_runtime/application/runner.py` - Core refactoring
- `agent/delivery/http/api/router.py` - Minor update
- `tests/test_api_router.py` - Update test expectations
- `tests/test_default_agent_tools.py` - Update test logic
