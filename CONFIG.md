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
- `~/.kaka-agent/skills/` - Custom skills

### 3. Cấu hình

Chỉnh sửa `~/.kaka-agent/config.yaml`:

```yaml
# LLM Provider
llm:
  default_provider: "openai-main"
  default_model: ""
  providers:
    openai-main:
      provider: "openai_compatible"
      api_key: "your-actual-api-key"
      base_url: "https://api.mistral.ai/v1"
      default_model: "devstral-2512"
    google-main:
      provider: "google"
      api_key: "your-google-api-key"
      default_model: "gemini-2.0-flash"

# Telegram Bot (optional)
channels:
  telegram:
    enabled: true
    update_mode: "polling"
    bot_token: "your-telegram-bot-token"
  discord:
    enabled: false
    bot_token: "your-discord-bot-token"
```

Với Telegram webhook production, dùng:

```yaml
enable_web: true
channels:
  telegram:
    enabled: true
    update_mode: "webhook"
    bot_token: "your-telegram-bot-token"
    webhook_url: "https://your-domain.example/channels/telegram/webhook"
    webhook_secret: "your-telegram-webhook-secret"
```

### 4. Chạy

```bash
kaka
# hoặc
kaka serve
```

## Configuration Priority

Hệ thống đọc cấu hình theo thứ tự ưu tiên:

1. **Config file** (`~/.kaka-agent/config.yaml`)
  Keys: `llm.default_provider`, `llm.default_model`, `llm.providers.*`, `database.url`, `channels.telegram.*`, `channels.discord.*`

2. **Defaults** (thấp nhất)

Runtime chỉ đọc cấu hình từ YAML file và defaults nội bộ.

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

### Sử dụng 1 default provider

```yaml
llm:
  default_provider: "openai-main"
  default_model: "gpt-4"
  providers:
    openai-main:
      provider: "openai_compatible"
      api_key: "sk-..."
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-4"
```

### Sử dụng multi-provider + default provider

```yaml
llm:
  default_provider: "google-main"
  providers:
    openai-main:
      provider: "openai_compatible"
      api_key: "sk-..."
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-4"
    google-main:
      provider: "google"
      api_key: "AIza..."
      default_model: "gemini-2.0-flash"
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
