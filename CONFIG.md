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

Chỉnh các cấu hình khởi động và credential hệ thống trong `~/.kaka-agent/config.yaml`.
Provider LLM, MCP servers, default model, policy channel runtime và `recursion_limit` được lưu trong DB và quản trị qua dashboard.

```yaml
host: "0.0.0.0"
port: 8000
enable_web: true
enable_api: true
enable_dashboard: true

channels:
  telegram:
    bot_token: "your-telegram-bot-token"
  discord:
    bot_token: "your-discord-bot-token"
  github:
    enabled: true
    app_id: "123456"
    app_slug: "your-github-app-slug"
    private_key_path: "~/.kaka-agent/github-app.pem"
    webhook_secret: "your-github-webhook-secret"
```

Với Telegram webhook production, dùng:

```yaml
enable_web: true
channels:
  telegram:
    bot_token: "your-telegram-bot-token"
    webhook_secret: "your-telegram-webhook-secret"
```

Đặt `channels.telegram.update_mode` và `channels.telegram.webhook_url` trong dashboard vì hai key này là runtime config trong DB.

Với GitHub App automation, dùng:

```yaml
channels:
  github:
    enabled: true
    app_id: "123456"
    app_slug: "your-github-app-slug"
    private_key_path: "~/.kaka-agent/github-app.pem"
    webhook_secret: "your-github-webhook-secret"
```

Đặt GitHub `default_agent`, `trigger_label`, `mention_triggers` trong dashboard.

GitHub App V1 dùng một app cho toàn instance, không cần `client_secret`. App cần quyền tối thiểu: Metadata read, Issues read/write, Contents read/write, Pull requests read/write. Bật webhook events: `issues`, `issue_comment`, `pull_request_review_comment`, `installation`, `installation_repositories`, `ping`. Webhook URL là `/channels/github/webhook`.

### 4. Chạy

```bash
kaka
# hoặc
kaka serve
```

## Configuration Priority

Hệ thống đọc cấu hình theo thứ tự ưu tiên:

1. **Defaults** (priority 0)

2. **Config file** (`~/.kaka-agent/config.yaml`, priority 100)
  Dành cho bootstrap, database URL, system credentials và secrets không thuộc runtime DB.

3. **Database** (priority 200)
  Dành cho `llm.default_model`, `llm.providers.*`, `mcp.servers.*`, selected channel policy và `recursion_limit`.

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

### Sử dụng provider

Vào dashboard Settings > Providers để tạo provider, nhập API key, base URL, default model, model list và chọn default provider.

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
