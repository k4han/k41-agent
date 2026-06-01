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

Chỉnh các cấu hình khởi động, database URL, JWT secret và display timezone trong `~/.kaka-agent/config.yaml`.
Provider LLM, MCP servers, default model, toàn bộ channel config và `recursion_limit` được lưu trong DB và quản trị qua dashboard.

```yaml
host: "0.0.0.0"
port: 8000
enable_web: true
enable_api: true
enable_dashboard: true
```

Vào dashboard `Settings > Channels` để cấu hình:
- `channels.telegram.*`: enabled, bot token, agent policy, update mode, webhook URL/secret.
- `channels.discord.*`: enabled, bot token, agent policy.
- `channels.github.*`: enabled, app ID/slug, private key hoặc private key path, webhook secret, default agent, trigger label, mention triggers.

Token, webhook secret và GitHub private key được mã hóa khi lưu trong DB. Nếu nâng cấp từ cấu hình YAML cũ, các runtime key channel còn thiếu trong DB sẽ được copy một lần từ YAML vào `runtime_settings`.

Với Telegram webhook production, bật `enable_web: true` trong YAML, rồi đặt `channels.telegram.update_mode=webhook`, `channels.telegram.webhook_url` và `channels.telegram.webhook_secret` trong dashboard.

Với GitHub App automation, đặt toàn bộ GitHub App config trong dashboard. GitHub App V1 dùng một app cho toàn instance, không cần `client_secret`. App cần quyền tối thiểu: Metadata read, Issues read/write, Contents read/write, Pull requests read/write. Bật webhook events: `issues`, `issue_comment`, `pull_request_review_comment`, `installation`, `installation_repositories`, `ping`. Webhook URL là `/channels/github/webhook`.

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
  Dành cho `llm.default_model`, `llm.providers.*`, `mcp.servers.*`, `channels.*` và `recursion_limit`.

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
