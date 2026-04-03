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
  api_key: "your-api-key-here"  # REQUIRED
  base_url: "https://api.mistral.ai/v1"
  model: "devstral-2512"
  temperature: 0.0

# Channel integrations (optional)
channels:
  telegram:
    bot_token: "your-telegram-bot-token-here"
    enabled: true
    # Optional agent overrides
    default_agent: "default"
    code_agent: "code-agent"
    research_agent: "research-agent"
  
  discord:
    bot_token: "your-discord-bot-token-here"
    enabled: true
    default_agent: "default"

# Paths
paths:
  agents: "~/.kaka-agent/agents"
  subagents: "~/.kaka-agent/subagents"
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
  api_key: "sk-your-actual-api-key"
```

Nếu không có API key hợp lệ, ứng dụng sẽ không khởi động được.

### Optional: Channel Tokens

Nếu muốn sử dụng Telegram hoặc Discord:

```yaml
channels:
  telegram:
    bot_token: "123456:ABC-DEF..."
  
  discord:
    bot_token: "MTk4NjIyNDgzNDcxOTI1MjQ4.Cl2FMQ..."
```

## Configuration Precedence

Config được load theo thứ tự ưu tiên:

1. **Default values** (priority: 0) - Hardcoded defaults
2. **YAML file** (priority: 100) - `~/.kaka-agent/config.yaml`
3. **Database** (priority: 200) - System config table (future)

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
  api_key: "sk-123"
  model: "gpt-4"
```

Truy cập bằng: `config.get_str("llm.api_key")`, `config.get_str("llm.model")`

## Migration from .env

Nếu bạn đang dùng `.env` file, hãy migrate sang `config.yaml`:

### Before (.env)
```bash
LLM_API_KEY=sk-xxx
LLM_MODEL=gpt-4
TELEGRAM_BOT_TOKEN=123:ABC
DATABASE_URL=sqlite:///data/db.sqlite
```

### After (config.yaml)
```yaml
llm:
  api_key: sk-xxx
  model: gpt-4

channels:
  telegram:
    bot_token: 123:ABC

database:
  url: sqlite+aiosqlite:///data/db.sqlite
```

## Validation

Khi khởi động, ứng dụng sẽ validate:

- LLM API key không được là placeholder ("your-api-key-here")
- Channel tokens (nếu channel được enable) không được là placeholder
- Database URL phải hợp lệ
- Paths phải tồn tại hoặc có thể tạo được

## Future: Database Configuration

Trong tương lai, config có thể được lưu vào database và edit qua dashboard UI:

```sql
CREATE TABLE system_config (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT,
    value_type VARCHAR(50),
    description TEXT
);
```

Database config sẽ có priority cao nhất và override YAML file.

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

### Database path error

```
ValueError: Database path escapes allowed directories
```

→ Set `persistence.allow_any_path: true` hoặc dùng path trong `~/.kaka-agent/`

## See Also

- [config.sample.yaml](../config.sample.yaml) - Sample configuration file
- [Architecture](./architecture.md) - System architecture overview
