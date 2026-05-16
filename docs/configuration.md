# Configuration Guide

## Overview

Kaka-agent sử dụng file `~/.kaka-agent/config.yaml` làm nguồn cấu hình duy nhất. Tất cả settings được quản lý tập trung qua ConfigService.

## Configuration File Location

```
~/.kaka-agent/config.yaml
```

Trên Windows: `C:\Users\<username>\.kaka-agent\config.yaml`
Trên Linux/Mac: `/home/<username>/.kaka-agent/config.yaml`

## Configuration Structure

```yaml
# Server configuration
host: "0.0.0.0"
port: 8000

# Feature flags
enable_web: true
enable_api: true
enable_dashboard: true

# Database configuration
database:
  url: "sqlite+aiosqlite://~/.kaka-agent/data/agent_state.db"
  # For PostgreSQL:
  # url: "postgresql+asyncpg://user:password@localhost:5432/kaka_agent"

# LLM Provider configuration
llm:
  # Multi-provider (recommended)
  default_provider: "openai-main"
  default_model: ""
  providers:
    openai-main:
      provider: "openai_compatible"
      api_key: "your-api-key-here"  # REQUIRED
      base_url: "https://api.mistral.ai/v1"
      default_model: "devstral-2512"
    google-main:
      provider: "google"
      api_key: "your-google-api-key"
      default_model: "gemini-2.0-flash"
  temperature: 0.0

# Channel integrations (optional)
channels:
  telegram:
    bot_token: "your-telegram-bot-token-here"
    enabled: true
    update_mode: "polling"
    # Required only when update_mode is "webhook"
    webhook_url: "https://your-domain.example/channels/telegram/webhook"
    webhook_secret: "your-telegram-webhook-secret"
    # Optional agent overrides
    default_agent: "default"
    code_agent: "code-agent"
    research_agent: "research-agent"
  
  discord:
    bot_token: "your-discord-bot-token-here"
    enabled: true
    default_agent: "default"

  github:
    enabled: true
    app_id: "123456"
    app_slug: "your-github-app-slug"
    private_key_path: "~/.kaka-agent/github-app.pem"
    webhook_secret: "your-github-webhook-secret"
    default_agent: "default"
    trigger_label: "kaka-agent"
    mention_triggers:
      - "@kaka-agent"
      - "/kaka"

# Paths
paths:
  agents: "~/.kaka-agent/agents"
  skills: "~/.kaka-agent/skills"
  data: "~/.kaka-agent/data"

# Security
persistence:
  allow_any_path: false  # Set to true to allow database outside safe directories
```

## Required Configuration

### LLM API Key (REQUIRED)

Bạn PHẢI cấu hình API key cho LLM provider:

```yaml
llm:
  default_provider: "openai-main"
  providers:
    openai-main:
      provider: "openai_compatible"
      api_key: "sk-your-actual-api-key"
```

Nếu không có API key hợp lệ, ứng dụng sẽ không khởi động được.

### Optional: Channel Tokens

Nếu muốn sử dụng Telegram hoặc Discord:

```yaml
channels:
  telegram:
    bot_token: "123456:ABC-DEF..."
    update_mode: "polling"
  
  discord:
    bot_token: "MTk4NjIyNDgzNDcxOTI1MjQ4.Cl2FMQ..."
```

Telegram có hai chế độ nhận update:

- `polling`: mặc định, phù hợp local/headless, chỉ cần `channels.telegram.bot_token`.
- `webhook`: cần `enable_web: true`, HTTPS public URL ở `channels.telegram.webhook_url`, và secret ở `channels.telegram.webhook_secret`. Endpoint nhận update là `/channels/telegram/webhook` và kiểm tra header `X-Telegram-Bot-Api-Secret-Token`.

## Configuration Precedence

Config được load theo thứ tự ưu tiên:

1. **Default values** (priority: 0) - Hardcoded defaults
2. **YAML file** (priority: 100) - `~/.kaka-agent/config.yaml`

Higher priority overrides lower priority.

## Accessing Configuration

### From Code

```python
from agent.shared.config import get_config_service

config = get_config_service()

# Typed getters
host = config.get_str("host", "0.0.0.0")
port = config.get_int("port", 8000)
enabled = config.get_bool("enable_web", True)
db_url = config.get_str("database.url")

# Path with ~ expansion
data_path = config.get_path("paths.data")

# Reload config from file
config.reload()
```

### Nested Keys

Nested YAML structures được flatten thành dot-notation:

```yaml
llm:
  default_provider: "openai-main"
  providers:
    openai-main:
      provider: "openai_compatible"
      api_key: "sk-123"
      default_model: "gpt-4"
```

Truy cập bằng: `config.get_str("llm.default_provider")`, `config.get_str("llm.providers.openai-main.api_key")`

## Provider Backends

### Multi-provider (recommended)

```yaml
llm:
  default_provider: "openai-main"
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
```

Với `google`, trường `base_url` sẽ bị bỏ qua.

## YAML-only Configuration

Ứng dụng chỉ đọc cấu hình từ `~/.kaka-agent/config.yaml`.
Ngoài YAML file, không có thêm nguồn runtime config nào khác.

## Validation

Khi khởi động, ứng dụng sẽ validate:

- LLM API key không được là placeholder ("your-api-key-here")
- Channel tokens (nếu channel được enable) không được là placeholder
- Database URL phải hợp lệ
- Paths phải tồn tại hoặc có thể tạo được

## Future: Database Configuration

Nếu bổ sung database config source trong tương lai, tài liệu này sẽ cập nhật thứ tự precedence tương ứng.

## Troubleshooting

### Config file not found

Nếu file không tồn tại, app sẽ dùng default values. Tạo file bằng:

```bash
mkdir -p ~/.kaka-agent
cp config.sample.yaml ~/.kaka-agent/config.yaml
```

### Invalid API key error

```
RuntimeError: LLM API key not configured
```

→ Kiểm tra `llm.api_key` trong config.yaml không phải placeholder

### Channel won't start

```
WARNING: Channel 'telegram' required config keys missing
```

→ Kiểm tra `channels.telegram.bot_token` đã được set

Nếu dùng Telegram webhook mà channel chuyển sang `error`, kiểm tra thêm `channels.telegram.webhook_url`, `channels.telegram.webhook_secret`, public HTTPS endpoint và reverse proxy.

### GitHub automation

GitHub App V1 dùng một app cho toàn instance, không dùng OAuth từng user nên không cần `client_secret`. Cấu hình bắt buộc:

```yaml
channels:
  github:
    enabled: true
    app_id: "123456"
    private_key_path: "~/.kaka-agent/github-app.pem"
    webhook_secret: "your-github-webhook-secret"
```

App cần quyền Metadata read, Issues read/write, Contents read/write, Pull requests read/write. Bật events `issues`, `issue_comment`, `installation`, `installation_repositories`, `ping`. Endpoint nhận webhook là `/channels/github/webhook`.

Repo được clone vào `~/kaka-agent/github-workspaces/{owner}/{repo}`. Khi issue/comment được trigger, backend chuẩn bị branch local, chạy agent trong working directory của repo, rồi commit/push/create PR bằng installation token.

### Database path error

```
ValueError: Database path escapes allowed directories
```

→ Set `persistence.allow_any_path: true` hoặc dùng path trong `~/.kaka-agent/`

## See Also

- [config.sample.yaml](../config.sample.yaml) - Sample configuration file
- [Architecture](./architecture.md) - System architecture overview
