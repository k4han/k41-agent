# LangGraph Multi-Platform Agent

Multi-platform AI agent dùng LangGraph, hỗ trợ FastAPI, Telegram, Discord trong cùng một runtime.

## Cấu trúc

```
agent/
├── bootstrap/        # App wiring, settings, runtime lifecycle
├── delivery/
│   └── http/
│       ├── api/      # FastAPI API router + schemas
│       └── dashboard/ # Dashboard HTTP router
├── shared/
│   └── infrastructure/
│       └── db/       # Shared DB primitives (URL, engine, session, metadata)
├── modules/
│   ├── agent_runtime/ # Platform-agnostic run facade
│   ├── channels/     # Telegram/Discord channel management
│   ├── settings/     # Persisted settings/preferences data access
│   └── workflows/    # LangGraph graphs, nodes, state, tools, checkpoint store
├── persistence/      # Compatibility shim for legacy persistence imports
├── core/             # Thin compatibility shim to agent_runtime
├── providers/
│   └── llm.py        # Cached LLM instances
├── graphs/           # Compatibility shim to workflows module
├── registry.py       # Compatibility shim to workflow registry
└── config.py         # Compatibility shim to workflow run config
```

## Thiết kế cốt lõi

### 1 graph — nhiều service khác nhau
```
Cùng workflow (chat/coding/research)
  → 1 graph instance
  → Khác nhau qua config (working_dir, service_type)
  → Chạy đồng thời, độc lập nhau ✅

Khác workflow (nodes/edges khác nhau)
  → Graph riêng, build 1 lần, lưu registry ✅
```

### Config vs State
```
config["configurable"]   → working_dir, service_type, thread_id
                           (bất biến trong run, không checkpoint)

state["messages"]        → nội dung hội thoại
                           (thay đổi được, được checkpoint)
```

### Checkpointer-level persistence
```
Graph compile có gắn checkpointer SQLite
  → state hội thoại được lưu theo thread_id
  → cùng thread_id sẽ tiếp tục ngữ cảnh sau mỗi request
  → không tạo message log table riêng ở giai đoạn này

Canonical ownership:
  - shared DB engine/session/helpers: agent/shared/infrastructure/db/
  - LangGraph checkpoint store: agent/modules/workflows/infrastructure/langgraph/checkpoint/
  - legacy facade tạm thời: agent/persistence/

DATABASE_URL mặc định:
  sqlite+aiosqlite:///data/agent_state.db
```

## Cài đặt

```bash
uv sync

cp .env.example .env
# Điền LLM_API_KEY vào .env
# Có thể tiếp tục dùng OPENAI_API_KEY như fallback tương thích ngược
# Có thể đổi DATABASE_URL nếu muốn lưu file SQLite ở vị trí khác
```

## Chạy

### App duy nhất
```bash
uv run python app.py
# Nếu ENABLE_WEB=true, server chạy tại http://localhost:8000
# Nếu ENABLE_WEB=false, app chạy headless và chỉ giữ các background channels
```

### Một số cờ cấu hình runtime
```env
ENABLE_WEB=true
ENABLE_API=true
ENABLE_DASHBOARD=true

ENABLE_TELEGRAM=true
ENABLE_DISCORD=false

LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.mistral.ai/v1
LLM_MODEL=devstral-2512
LLM_TEMPERATURE=0
```

Quy ước hiện tại:
- `ENABLE_WEB`, `ENABLE_API`, `ENABLE_DASHBOARD`: bật các capability của web host khi app khởi động.
- `ENABLE_TELEGRAM`, `ENABLE_DISCORD`: nếu `true` thì channel sẽ tự khởi động cùng app. Các background channel vẫn luôn được đăng ký vào runtime, nên dashboard vẫn có thể start/stop chúng về sau ngay cả khi giá trị này là `false`.
- Dashboard chỉ thay đổi trạng thái runtime hiện tại. Khi restart app, trạng thái mặc định quay về theo `.env`.
- Alias cũ `/dashboard/bots/*` đã bị loại bỏ. Chỉ dùng `/dashboard/services/*`.
- LLM client hiện dùng `ChatOpenAI` với endpoint OpenAI-compatible. Mặc định repo trỏ tới Mistral-compatible `base_url` và `model`, nhưng có thể override bằng `LLM_BASE_URL` và `LLM_MODEL`.
- `LLM_API_KEY` là contract ưu tiên. Nếu chưa migrate env, `OPENAI_API_KEY` vẫn được chấp nhận như fallback.

## API Endpoints

| Method | Endpoint          | Mô tả                    |
|--------|-------------------|--------------------------|
| POST   | /api/chat         | Chat sync                |
| POST   | /api/chat/stream  | Chat với streaming       |
| GET    | /api/graphs       | Liệt kê graphs           |
| GET    | /api/health       | Health check             |

## Dashboard Endpoints

| Method | Endpoint                           | Mô tả                         |
|--------|------------------------------------|-------------------------------|
| GET    | /dashboard/services                | Liệt kê trạng thái services   |
| GET    | /dashboard/services/{name}         | Xem trạng thái 1 service      |
| POST   | /dashboard/services/{name}/start   | Bật 1 service                 |
| POST   | /dashboard/services/{name}/stop    | Tắt 1 service                 |
| POST   | /dashboard/services/start-all      | Bật tất cả services           |
| POST   | /dashboard/services/stop-all       | Tắt tất cả services           |

### Ví dụ request

```bash
# Chat thông thường
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Xin chào!",
    "user_id": "user_123",
    "workflow": "chat_agent",
    "service_type": "default"
  }'

# Coding agent với working_dir
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Liệt kê các file trong thư mục",
    "user_id": "dev_456",
    "workflow": "coding_agent",
    "service_type": "backend",
    "working_dir": "/home/myproject"
  }'

# Research
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Phân tích ưu nhược điểm microservices vs monolith",
    "user_id": "user_789",
    "workflow": "research_chain"
  }'

# Streaming
curl -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Giải thích LangGraph", "user_id": "user_1"}'
```

## Thêm platform mới (Slack, Zalo,...)

1. Tạo `agent/modules/channels/infrastructure/slack/handler.py`
2. Gọi `build_run_params()` + `run_agent_full()` từ `agent.modules.agent_runtime.public`
3. Thêm `ChannelSpec` mới vào `agent/modules/channels/infrastructure/service_specs.py`
4. **Không cần** động vào `delivery/http` hay `modules/workflows`

## Thêm workflow mới

1. Tạo graph mới trong `agent/modules/workflows/infrastructure/langgraph/graphs/`
2. Đăng ký trong `agent/modules/workflows/application/register_builtin_workflows.py`
3. **Không cần** động vào `delivery/http` hay `modules/channels`

## Workflows

| Workflow         | Mô tả                              | Tools                              |
|------------------|------------------------------------|------------------------------------|
| `chat_agent`     | Hỏi đáp thông thường               | get_current_time, echo             |
| `coding_agent`   | Đọc/ghi file, chạy bash            | read_file, write_file, run_bash, list_files |
| `research_chain` | Nghiên cứu, tổng hợp (2 bước)      | (LLM only)                         |
| `router`         | Tự phân loại → chuyển đúng workflow | (LLM classifier)                   |
