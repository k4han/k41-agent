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
pnpm install
uv run kaka init
# Mở dashboard và thêm provider tại Settings > Providers
```

## Dashboard frontend

Dashboard hiện là SPA `Solid + Vite`, được FastAPI serve từ static build:

```bash
pnpm dashboard:check
pnpm dashboard:build
uv run python app.py
```

- Source frontend: `agent/delivery/http/dashboard/frontend`
- Static build được xuất ra: `agent/delivery/http/dashboard/static`
- Assets được serve tại `/dashboard-assets`
- Các route dashboard như `/`, `/agents`, `/chat`, `/tasks`, `/scheduler`, `/config`, `/providers` trả cùng SPA shell.
- Dữ liệu đọc cho dashboard nằm dưới `/dashboard-api/*`; các mutation cũ như `/agents/cards`, `/settings`, `/scheduler/jobs`, `/tasks` vẫn được giữ.

## Chạy

### App duy nhất
```bash
uv run python app.py
# Nếu ENABLE_WEB=true, server chạy tại http://localhost:8000
# Nếu ENABLE_WEB=false, app chạy headless và chỉ giữ các background channels
```

### Một số cấu hình khởi động
```yaml
enable_web: true
enable_api: true
enable_dashboard: true
```

Quy ước hiện tại:
- `enable_web`, `enable_api`, `enable_dashboard`: bật các capability của web host khi app khởi động.
- `database.url`, `display.timezone` vẫn đọc từ `~/.kaka-agent/config.yaml`; `security.jwt_secret` là secret nội bộ và app tự sinh khi thiếu.
- `llm.providers.*`, `llm.default_model`, `mcp.servers.*`, `channels.*` và `recursion_limit` được lưu trong DB để dashboard quản trị runtime. Provider API key, MCP `env.*`/`headers.*`, channel token/secret và GitHub private key được mã hóa khi lưu.
- Cấu hình Telegram/Discord/GitHub nằm ở Settings > Channels. Nếu enabled và đủ credential thì channel sẽ tự khởi động cùng app.
- Nếu dùng Telegram webhook, cần `enable_web: true`, `channels.telegram.webhook_url` trỏ tới `/channels/telegram/webhook`, và `channels.telegram.webhook_secret` trong dashboard để kiểm tra header `X-Telegram-Bot-Api-Secret-Token`.
- Khi nâng cấp từ YAML cũ, runtime key channel còn thiếu trong DB sẽ được seed một lần từ `~/.kaka-agent/config.yaml`.
- Với dashboard chạy ở prefix gốc `/`, alias cũ `/bots/*` đã bị loại bỏ. Chỉ dùng `/services/*`.
- LLM config dùng `llm.providers.*` + `llm.default_model` trong DB; `llm.default_provider` cũ chỉ còn là giá trị tổng hợp cho UI.
- Backend đang hỗ trợ: `openai_compatible` (dùng `ChatOpenAI`) và `google` (dùng `ChatGoogleGenerativeAI`, bỏ qua `base_url`).
- Model mặc định được resolve theo thứ tự: request override -> agent card -> `provider.default_model`.
- Dropdown model lấy từ `llm.providers.<name>.models`, cộng thêm `default_model`; endpoint hỗ trợ refresh live nếu provider có API list model.
- API key provider được lưu tại `llm.providers.<name>.api_key` trong DB và mã hóa.

## API Endpoints

| Method | Endpoint          | Mô tả                    |
|--------|-------------------|--------------------------|
| POST   | /api/chat         | Chat sync                |
| POST   | /api/chat/stream  | Chat với streaming       |
| GET    | /api/graphs       | Liệt kê graphs           |
| GET    | /api/providers    | Liệt kê providers        |
| GET    | /api/providers/models | Liệt kê model options |
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

1. Tạo adapter trong `agent/modules/channels/<platform>/adapter.py` implement `ChatChannelAdapter`.
2. Adapter khai báo `settings_schema`, `settings_sections`, `capabilities`, `create_runner()`, `send()` và normalize event thành `InboundMessage`.
3. Gửi message vào pipeline chung bằng `process_inbound_message()`. Pipeline đã xử lý auth, `/pair`, command registry, streaming agent và default agent.
4. Thêm `ChannelSpec` trong `agent/modules/channels/service_specs.py` trỏ tới adapter loader và runner.
5. Nếu platform hỗ trợ command suggestion như Telegram, implement `sync_commands()` để map `CommandRegistry` sang API native.
6. **Không cần** động vào `agent_runtime`, `workflows` hoặc dashboard UI cho các field settings cơ bản; dashboard đọc schema từ adapter catalog.

## Thêm workflow mới

1. Tạo graph mới trong `agent/modules/workflows/infrastructure/langgraph/graphs/`
2. Đăng ký trong `agent/modules/workflows/application/register_builtin_workflows.py`
3. **Không cần** động vào `delivery/http` hay `modules/channels`

## Workflows

| Workflow         | Mô tả                              | Tools                              |
|------------------|------------------------------------|------------------------------------|
| `react_agent`    | Hỏi đáp + coding + file/bash + skills | get_current_time, echo, skill, read_file, write_file, bash, bash_send_input, bash_interrupt, list_dir |
| `research_chain` | Nghiên cứu, tổng hợp (2 bước)      | (LLM only)                         |
| `router`         | Tự phân loại → chuyển đúng workflow | (LLM classifier)                   |

> **Chú ý về `bash` tools:** Các tool `bash`, `bash_send_input`, `bash_interrupt` hoạt động theo **session trong từng conversation thread** (mặc định `session_id="default"`). Trong cùng một thread, trạng thái terminal được giữ nguyên qua nhiều lần gọi: `cd` thay đổi thư mục hiện tại, biến môi trường được set tồn tại đến lần gọi sau, và tiến trình nền vẫn chạy. Thread mới sẽ có shell session riêng dù dùng cùng `session_id`; nếu muốn tách thêm bên trong cùng một thread, agent có thể chỉ định `session_id` khác.

Các tool file/bash làm việc trực tiếp với đường dẫn vật lý tuyệt đối của workspace; path tương đối vẫn được chấp nhận nếu nằm trong workspace.
