# Kaka Agent Configuration Guide

## Quick Start

### 1. Cài đặt

```bash
uv tool install --force .
```

### 2. Khởi tạo

```bash
kaka init
```

Lệnh này sẽ tạo:
- `~/.kaka-agent/` - Thư mục chính
- `~/.kaka-agent/config.yaml` - File cấu hình
- `~/.kaka-agent/data/` - Database và dữ liệu
- `~/.kaka-agent/agents/` - Custom agents
- `~/.kaka-agent/subagents/` - Sub-agents
- `~/.kaka-agent/skills/` - Custom skills

### 3. Cấu hình

Chỉnh sửa `~/.kaka-agent/config.yaml`:

```yaml
# LLM Provider
llm:
  api_key: "your-actual-api-key"  # Thay bằng API key thật
  base_url: "https://api.mistral.ai/v1"
  model: "devstral-2512"

# Telegram Bot (optional)
channels:
  telegram:
    bot_token: "your-telegram-bot-token"

# Discord Bot (optional)
channels:
  discord:
    bot_token: "your-discord-bot-token"
```

### 4. Chạy

```bash
kaka
# hoặc
kaka serve
```

## Configuration Priority

Hệ thống đọc cấu hình theo thứ tự ưu tiên:

1. **Environment variables** (cao nhất)
   - `LLM_API_KEY`, `OPENAI_API_KEY`
   - `LLM_BASE_URL`, `LLM_MODEL`
   - `DATABASE_URL`
   - `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`

2. **Config file** (`~/.kaka-agent/config.yaml`)
   - `llm.api_key`, `llm.base_url`, `llm.model`
   - `database.url`
   - `channels.telegram.bot_token`, `channels.discord.bot_token`

3. **Defaults** (thấp nhất)

## Backward Compatibility

Hệ thống vẫn tương thích với `.env` file. Nếu bạn có `.env` với các biến:
- `LLM_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `DISCORD_BOT_TOKEN`

Chúng sẽ được ưu tiên hơn config file.

## Database Configuration

### SQLite (default)
```yaml
database:
  url: "sqlite+aiosqlite://~/.kaka-agent/data/agent_state.db"
```

### PostgreSQL
```yaml
database:
  url: "postgresql+asyncpg://user:password@localhost:5432/kaka_agent"
```

## Examples

### Sử dụng OpenAI thay vì Mistral

```yaml
llm:
  api_key: "sk-..."
  base_url: "https://api.openai.com/v1"
  model: "gpt-4"
```

### Chỉ chạy API, tắt dashboard

```yaml
enable_api: true
enable_dashboard: false
enable_web: false
```

### Custom port

```yaml
host: "127.0.0.1"
port: 3000
```
