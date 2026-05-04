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
│   └── workflows/    # LangGraph graphs, nodes, state, tools, checkpoint store
└── providers/
    └── llm.py        # Cached LLM instances
```

## Thiết kế cốt lõi

### 1 graph — nhiều service khác nhau
```
Cùng workflow (react/research)
  → 1 graph instance
  → Khác nhau qua context (working_dir, service_type)
  → Chạy đồng thời, độc lập nhau ✅

Khác workflow (nodes/edges khác nhau)
  → Graph riêng, build 1 lần, lưu registry ✅
```

### Config vs Context vs State
```
config["configurable"]   → thread_id
                           (khóa checkpoint thread)

context                   → working_dir, service_type, max_context_tokens
                           (run-scoped runtime knobs, không checkpoint)

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

database.url mặc định:
  sqlite+aiosqlite:///data/agent_state.db
```

## Cài đặt

```bash
uv sync
uv run kaka init
# Chỉnh ~/.kaka-agent/config.yaml
# Bắt buộc set llm.api_key
```

## Chạy

### App duy nhất
```bash
uv run python app.py
# Nếu ENABLE_WEB=true, server chạy tại http://localhost:8000
# Nếu ENABLE_WEB=false, app chạy headless và chỉ giữ các background channels
```

### Một số cờ cấu hình runtime
```yaml
enable_web: true
enable_api: true
enable_dashboard: true

channels:
  telegram:
    enabled: true
  discord:
    enabled: false

llm:
  default_provider: "openai-main"
  default_model: ""
  providers:
    openai-main:
      provider: "openai_compatible"
      api_key: "sk-..."
      base_url: "https://api.mistral.ai/v1"
      default_model: "devstral-2512"
    google-main:
      provider: "google"
      api_key: "AIza..."
      default_model: "gemini-2.0-flash"
  temperature: 0.0
```

Quy ước hiện tại:
- `enable_web`, `enable_api`, `enable_dashboard`: bật các capability của web host khi app khởi động.
- `channels.telegram.enabled`, `channels.discord.enabled`: nếu `true` thì channel sẽ tự khởi động cùng app. Các background channel vẫn luôn được đăng ký vào runtime, nên dashboard vẫn có thể start/stop chúng về sau ngay cả khi giá trị này là `false`.
- Dashboard chỉ thay đổi trạng thái runtime hiện tại. Khi restart app, trạng thái mặc định quay về theo `~/.kaka-agent/config.yaml`.
- Với dashboard chạy ở prefix gốc `/`, alias cũ `/bots/*` đã bị loại bỏ. Chỉ dùng `/services/*`.
- LLM config dùng chuẩn `llm.providers.*` + `llm.default_provider` + `llm.default_model`.
- Backend đang hỗ trợ: `openai_compatible` (dùng `ChatOpenAI`) và `google` (dùng `ChatGoogleGenerativeAI`, bỏ qua `base_url`).
- Model mặc định được resolve theo thứ tự: `provider.default_model` -> `llm.default_model` -> fallback nội bộ theo loại provider.
- API key được resolve theo thứ tự: `llm.providers.<name>.api_key` -> `llm.api_key`.

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
| GET    | /services                          | Liệt kê trạng thái services   |
| GET    | /services/{name}                   | Xem trạng thái 1 service      |
| POST   | /services/{name}/start             | Bật 1 service                 |
| POST   | /services/{name}/stop              | Tắt 1 service                 |
| POST   | /services/start-all                | Bật tất cả services           |
| POST   | /services/stop-all                 | Tắt tất cả services           |

### Ví dụ request

```bash
# Chat thông thường
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Xin chào!",
    "user_id": "user_123",
    "workflow": "react_agent",
    "service_type": "default"
  }'

# React agent với service_type backend + working_dir
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Liệt kê các file trong thư mục",
    "user_id": "dev_456",
    "workflow": "react_agent",
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
2. Gọi `build_run_params()` + `run_agent_full()` từ `agent.modules.agent_runtime`
3. Thêm `ChannelSpec` mới vào `agent/modules/channels/infrastructure/service_specs.py`
4. **Không cần** động vào `delivery/http` hay `modules/workflows`

## Thêm workflow mới

1. Tạo graph mới trong `agent/modules/workflows/infrastructure/langgraph/graphs/`
2. Đăng ký trong `agent/modules/workflows/application/register_builtin_workflows.py`
3. **Không cần** động vào `delivery/http` hay `modules/channels`

## Workflows

| Workflow         | Mô tả                              | Tools                              |
|------------------|------------------------------------|------------------------------------|
| `react_agent`    | Hỏi đáp + coding + file/bash + skills | get_current_time, echo, skill, read_file, write_file, run_bash, list_files |
| `research_chain` | Nghiên cứu, tổng hợp (2 bước)      | (LLM only)                         |
| `router`         | Tự phân loại → chuyển đúng workflow | (LLM classifier)                   |
