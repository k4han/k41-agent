# Migration Guide: From .env to config.yaml

## Overview

Kaka-agent v2 đã loại bỏ hoàn toàn support cho `.env` files. Tất cả configuration giờ được quản lý qua `~/.kaka-agent/config.yaml`.

## Why This Change?

**Lý do loại bỏ .env:**
- ❌ Risk commit nhầm secrets vào git
- ❌ Config phân tán (env vars, file, hardcoded)
- ❌ Khó quản lý và maintain
- ❌ Không có path rõ ràng để migrate lên database

**Lợi ích của config.yaml:**
- ✅ Tách biệt config khỏi source code
- ✅ Chuẩn hóa vị trí config (`~/.kaka-agent/`)
- ✅ Dễ migrate lên database sau này
- ✅ User-specific config, không conflict khi pull code
- ✅ Phù hợp với pattern của CLI applications

## Migration Steps

### Step 1: Locate Your .env File

Nếu bạn đang dùng `.env` file trong project root, hãy mở nó ra.

### Step 2: Create config.yaml

Tạo file mới tại `~/.kaka-agent/config.yaml`:

```bash
mkdir -p ~/.kaka-agent
touch ~/.kaka-agent/config.yaml
```

### Step 3: Map Environment Variables to YAML

Dưới đây là bảng mapping từ env vars sang config keys:

| Old (.env) | New (config.yaml) | Example |
|------------|-------------------|---------|
| `HOST` | `host` | `host: "0.0.0.0"` |
| `PORT` | `port` | `port: 8000` |
| `ENABLE_WEB` | `enable_web` | `enable_web: true` |
| `ENABLE_API` | `enable_api` | `enable_api: true` |
| `ENABLE_DASHBOARD` | `enable_dashboard` | `enable_dashboard: true` |
| `DATABASE_URL` | `database.url` | `database:\n  url: "sqlite+aiosqlite://..."` |
| `PERSISTENCE_ALLOW_ANY_PATH` | `persistence.allow_any_path` | `persistence:\n  allow_any_path: false` |
| `LLM_API_KEY` | `llm.api_key` | `llm:\n  api_key: "sk-xxx"` |
| `LLM_BASE_URL` | `llm.base_url` | `llm:\n  base_url: "https://..."` |
| `LLM_MODEL` | `llm.model` | `llm:\n  model: "gpt-4"` |
| `LLM_TEMPERATURE` | `llm.temperature` | `llm:\n  temperature: 0.0` |
| `TELEGRAM_BOT_TOKEN` | `channels.telegram.bot_token` | `channels:\n  telegram:\n    bot_token: "123:ABC"` |
| `DISCORD_BOT_TOKEN` | `channels.discord.bot_token` | `channels:\n  discord:\n    bot_token: "MTk4..."` |
| `ENABLE_TELEGRAM` | `channels.telegram.enabled` | `channels:\n  telegram:\n    enabled: true` |
| `ENABLE_DISCORD` | `channels.discord.enabled` | `channels:\n  discord:\n    enabled: true` |

### Step 4: Example Migration

**Before (.env):**
```bash
# Server
HOST=0.0.0.0
PORT=8000
ENABLE_WEB=true

# Database
DATABASE_URL=sqlite+aiosqlite:///home/user/.kaka-agent/data/agent_state.db

# LLM
LLM_API_KEY=sk-proj-abc123xyz
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4
LLM_TEMPERATURE=0.0

# Channels
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
ENABLE_TELEGRAM=true
```

**After (config.yaml):**
```yaml
# Server configuration
host: "0.0.0.0"
port: 8000
enable_web: true
enable_api: true
enable_dashboard: true

# Database configuration
database:
  url: "sqlite+aiosqlite://~/.kaka-agent/data/agent_state.db"

# LLM Provider configuration
llm:
  api_key: "sk-proj-abc123xyz"
  base_url: "https://api.openai.com/v1"
  model: "gpt-4"
  temperature: 0.0

# Channel integrations
channels:
  telegram:
    bot_token: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    enabled: true
```

### Step 5: Remove .env File

Sau khi migrate xong và test thành công:

```bash
rm .env
```

**Quan trọng:** Đảm bảo `.env` đã được add vào `.gitignore` để không commit nhầm.

### Step 6: Test Configuration

Khởi động lại ứng dụng:

```bash
uv run kaka-agent serve
```

Kiểm tra logs để đảm bảo:
- ✅ Config được load từ `~/.kaka-agent/config.yaml`
- ✅ LLM provider khởi tạo thành công
- ✅ Database connection hoạt động
- ✅ Channels (nếu có) khởi động thành công

## Removed Environment Variables

Các env vars sau đây **KHÔNG còn được support**:

- `HOST`, `PORT`
- `ENABLE_WEB`, `ENABLE_API`, `ENABLE_DASHBOARD`
- `DATABASE_URL`, `PERSISTENCE_ALLOW_ANY_PATH`
- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE`
- `OPENAI_API_KEY` (fallback)
- `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`
- `ENABLE_TELEGRAM`, `ENABLE_DISCORD`
- `KAKA_TELEGRAM_DEFAULT_AGENT`, `KAKA_TELEGRAM_CODE_AGENT`, `KAKA_TELEGRAM_RESEARCH_AGENT`
- `KAKA_DISCORD_DEFAULT_AGENT`
- `KAKA_AGENTS_DIR`
- `WORKING_DIR`

Tất cả phải được config trong `config.yaml`.

## Special Cases

### Path Expansion

Trong config.yaml, bạn có thể dùng `~` để refer đến home directory:

```yaml
database:
  url: "sqlite+aiosqlite://~/.kaka-agent/data/agent_state.db"

paths:
  agents: "~/.kaka-agent/agents"
```

Hệ thống sẽ tự động expand `~` thành absolute path.

### Boolean Values

YAML hỗ trợ nhiều cách viết boolean:

```yaml
enable_web: true    # Recommended
enable_web: yes
enable_web: on
enable_web: 1
```

### Sensitive Values

**Lưu ý:** `config.yaml` chứa sensitive data (API keys, tokens). Đảm bảo:

1. File có permissions đúng:
   ```bash
   chmod 600 ~/.kaka-agent/config.yaml
   ```

2. KHÔNG commit file này vào git

3. Backup file này an toàn

## Troubleshooting

### "Config file not found"

Nếu thấy warning này, app sẽ dùng default values. Tạo file:

```bash
cp config.sample.yaml ~/.kaka-agent/config.yaml
```

### "LLM API key not configured"

```
RuntimeError: LLM API key not configured.
Please set 'llm.api_key' in ~/.kaka-agent/config.yaml
```

→ Kiểm tra `llm.api_key` trong config.yaml

### Environment variables still being read

Nếu bạn vẫn thấy env vars được đọc, có thể:

1. Bạn đang dùng version cũ của code
2. Pull latest changes: `git pull origin main`
3. Reinstall dependencies: `uv sync`

### Config changes not taking effect

Config được cache. Để reload:

```python
from agent.shared.config import reload_config
reload_config()
```

Hoặc restart ứng dụng.

## Getting Help

Nếu gặp vấn đề khi migrate:

1. Kiểm tra [configuration.md](./configuration.md) để xem cấu trúc đúng
2. Xem [config.sample.yaml](../config.sample.yaml) để tham khảo
3. Mở issue trên GitHub với thông tin chi tiết

## Summary

✅ **DO:**
- Dùng `~/.kaka-agent/config.yaml` cho tất cả config
- Set permissions đúng cho config file
- Backup config file
- Dùng `~` cho home directory paths

❌ **DON'T:**
- Dùng `.env` files (không còn support)
- Set config qua environment variables
- Commit config.yaml vào git
- Share config file chứa secrets
