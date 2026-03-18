# LangGraph Multi-Platform Agent

Multi-platform AI agent dùng LangGraph, hỗ trợ FastAPI, Telegram, Discord.

## Cấu trúc

```
agent/
├── graphs/           # Graph definitions (build 1 lần, reuse mãi)
│   ├── __init__.py   # setup_all_graphs()
│   ├── chat_agent.py
│   ├── coding_agent.py
│   ├── research_chain.py
│   └── router.py     # Tự phân loại workflow
├── nodes/            # Shared reusable nodes
│   ├── llm_node.py
│   └── tool_node.py
├── state/
│   ├── base.py       # BaseState (MessagesState)
│   └── extensions.py # CodingState, ResearchState
├── tools/
│   ├── common.py     # read_file, write_file, run_bash, list_files
│   └── chat.py       # get_current_time, echo
├── core/
│   ├── runner.py     # run_agent(), run_agent_full() — platform agnostic
│   └── session.py    # thread_id management
├── adapters/
│   ├── base.py       # BaseAdapter interface
│   ├── fastapi/      # FastAPI router + schemas
│   ├── telegram/     # Telegram bot handler
│   └── discord/      # Discord bot handler
├── providers/
│   └── llm.py        # Cached LLM instances
├── registry.py       # GraphRegistry
└── config.py         # make_config()
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

DATABASE_URL mặc định:
  sqlite:///./agent_state.db
```

## Cài đặt

```bash
pip install -r requirements.txt

cp .env.example .env
# Điền OPENAI_API_KEY vào .env
# Có thể đổi DATABASE_URL nếu muốn lưu file SQLite ở vị trí khác
```

## Chạy

### FastAPI server
```bash
python main.py
# Server chạy tại http://localhost:8000
```

### Telegram bot (standalone)
```bash
# Điền TELEGRAM_BOT_TOKEN vào .env
python run_telegram.py
```

### Discord bot (standalone)
```bash
# Điền DISCORD_BOT_TOKEN vào .env
python run_discord.py
```

### Test local (không cần server)
```bash
python test_local.py
```

## API Endpoints

| Method | Endpoint          | Mô tả                    |
|--------|-------------------|--------------------------|
| POST   | /api/chat         | Chat sync                |
| POST   | /api/chat/stream  | Chat với streaming       |
| GET    | /api/graphs       | Liệt kê graphs           |
| GET    | /api/health       | Health check             |

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

1. Tạo `agent/adapters/slack/handler.py`
2. Kế thừa `BaseAdapter`, implement `handle()`
3. Gọi `normalize()` + `run_agent_full()`
4. **Không cần** động vào `core/`, `graphs/`, hay `registry.py`

## Thêm workflow mới

1. Tạo `agent/graphs/new_agent.py` → build + `GraphRegistry.register()`
2. Import vào `agent/graphs/__init__.py` → thêm vào `setup_all_graphs()`
3. **Không cần** động vào adapters hay core

## Workflows

| Workflow         | Mô tả                              | Tools                              |
|------------------|------------------------------------|------------------------------------|
| `chat_agent`     | Hỏi đáp thông thường               | get_current_time, echo             |
| `coding_agent`   | Đọc/ghi file, chạy bash            | read_file, write_file, run_bash, list_files |
| `research_chain` | Nghiên cứu, tổng hợp (2 bước)      | (LLM only)                         |
| `router`         | Tự phân loại → chuyển đúng workflow | (LLM classifier)                   |
