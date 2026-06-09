# Configuration Guide

## Overview

Kai-agent sử dụng `~/.k41-agent/config.yaml` cho cấu hình bootstrap và dùng bảng `runtime_settings` trong DB cho cấu hình runtime quản trị qua dashboard. Tất cả settings vẫn được đọc tập trung qua ConfigService.

## Configuration File Location

```
~/.k41-agent/config.yaml
```

Trên Windows: `C:\Users\<username>\.k41-agent\config.yaml`
Trên Linux/Mac: `/home/<username>/.k41-agent/config.yaml`

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
  url: "sqlite+aiosqlite://~/.k41-agent/data/agent_state.db"
  # For PostgreSQL:
  # url: "postgresql+asyncpg://user:password@localhost:5432/k41_agent"

# Runtime settings
# Configure LLM providers, MCP servers, channels, and recursion_limit
# from the dashboard. Channel settings live at Settings > Channels.

# Paths
paths:
  agents: "~/.k41-agent/agents"
  skills: "~/.k41-agent/skills"
  data: "~/.k41-agent/data"

# Security
persistence:
  allow_any_path: false  # Set to true to allow database outside safe directories
```

## Required Configuration

### LLM API Key (REQUIRED)

Bạn PHẢI cấu hình API key cho LLM provider:

Vào dashboard `Settings > Providers`, tạo provider, nhập API key, default model, model list và chọn default provider.

Nếu không có API key hợp lệ, ứng dụng sẽ không khởi động được.

### Optional: Channel Tokens

Nếu muốn sử dụng Telegram, Discord hoặc GitHub App, vào dashboard `Settings > Channels`.

Telegram có hai chế độ nhận update:

- `polling`: mặc định, phù hợp local/headless, chỉ cần `channels.telegram.bot_token`.
- `webhook`: cần `enable_web: true`, HTTPS public URL ở `channels.telegram.webhook_url`, và secret ở `channels.telegram.webhook_secret`. Endpoint nhận update là `/channels/telegram/webhook` và kiểm tra header `X-Telegram-Bot-Api-Secret-Token`.

Channel token, webhook secret và GitHub private key được mã hóa trong DB. Nếu nâng cấp từ YAML cũ, các key `channels.*` còn thiếu trong DB sẽ được copy một lần từ YAML.

Các chat channel mới nên khai báo field qua `ChatChannelAdapter.settings_schema`. Dashboard Settings > Channels đọc schema này để render form, kiểm tra required credential và gửi notification qua adapter `send()` thay vì thêm nhánh hard-code trong notification service.

## Configuration Precedence

Config được load theo thứ tự ưu tiên:

1. **Default values** (priority: 0) - Hardcoded defaults
2. **YAML file** (priority: 100) - `~/.k41-agent/config.yaml`
3. **Database** (priority: 200) - runtime settings quản trị qua dashboard

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

`security.jwt_secret` là secret nội bộ dùng để ký JWT admin. Nếu thiếu hoặc rỗng, app sẽ tự sinh và lưu vào `~/.k41-agent/config.yaml`; key này không được quản trị qua dashboard.

## Provider Backends

### Multi-provider (recommended)

Vào dashboard `Settings > Providers` để cấu hình nhiều provider.

Với `google`, trường `base_url` sẽ bị bỏ qua.

## Runtime Database Configuration

Các key `llm.*`, `mcp.servers.*`, `channels.*` và `recursion_limit` được lưu trong DB. Dashboard ghi qua endpoint `/settings`; ConfigService đọc DB với priority cao hơn YAML.

## Validation

Khi khởi động, ứng dụng sẽ validate:

- LLM API key không được là placeholder ("your-api-key-here")
- Channel tokens/secrets (nếu channel được enable) không được là placeholder
- Database URL phải hợp lệ
- Paths phải tồn tại hoặc có thể tạo được

## Troubleshooting

### Config file not found

Nếu file không tồn tại, app sẽ dùng default values. Tạo file bằng:

```bash
mkdir -p ~/.k41-agent
cp config.sample.yaml ~/.k41-agent/config.yaml
```

### Invalid API key error

```
RuntimeError: LLM API key not configured
```

→ Kiểm tra provider API key trong dashboard Settings > Providers.

### Channel won't start

```
WARNING: Channel 'telegram' required config keys missing
```

→ Kiểm tra `channels.telegram.bot_token` đã được set

→ Với cấu hình runtime mới, kiểm tra Settings > Channels.

Nếu dùng Telegram webhook mà channel chuyển sang `error`, kiểm tra thêm `channels.telegram.webhook_url`, `channels.telegram.webhook_secret`, public HTTPS endpoint và reverse proxy.

### GitHub automation

GitHub App V1 dùng một app cho toàn instance, không dùng OAuth từng user nên không cần `client_secret`. Cấu hình bắt buộc:

Đặt `channels.github.enabled`, `app_id`, `private_key` hoặc `private_key_path`, và `webhook_secret` trong dashboard Settings > Channels.

App cần quyền Metadata read, Issues read/write, Contents read/write, Pull requests read/write. Bật events `issues`, `issue_comment`, `pull_request_review_comment`, `installation`, `installation_repositories`, `ping`. Endpoint nhận webhook là `/channels/github/webhook`.

Repo được clone vào `~/k41-agent/github-workspaces/{owner}/{repo}`. Khi issue/comment được trigger, backend chuẩn bị branch local, chạy agent trong working directory của repo, rồi commit/push/create PR bằng installation token. Khi có `pull_request_review_comment`, backend checkout branch đang mở PR, chạy agent với ngữ cảnh review comment, rồi commit/push lại cùng PR.

#### GitHub repo trong sandbox (Daytona / Modal)

Dashboard Workspace selector là UI 2 cấp:

1. **Backend**: `local` (host filesystem) | `daytona` | `modal`.
2. **Source**: tuỳ backend — `local` có `folder`, `daytona`/`modal` có `sandbox` và `github-repo`.

Khi chọn backend `daytona` hoặc `modal` + source `github-repo`, dashboard gọi `POST /dashboard-api/workspace/resolve` với `kind="github"`, `backend="daytona"|"modal"`, `repository_id=<id>`. Backend sẽ:

- Tạo (hoặc attach) sandbox tương ứng.
- Clone repo vào trong sandbox bằng `git clone --depth 1 --branch <default_branch> --single-branch` với installation token (nếu có). Vị trí: `{sandbox_root}/{owner}/{repo}`.
- Trả về `WorkspaceRef` với `metadata.source="github"`, `metadata.repository_full_name`, `metadata.repository_path`, và label là `owner/repo`.

Local backend giữ hành vi cũ: clone về `~/k41-agent/github-workspaces/{owner}/{repo}` và resolve qua `GitHubWorkspaceManager.ensure_shared_checkout`. Tương thích ngược với payload `kind="github"` không có `backend` (vẫn trả về workspace local).

Push về GitHub vẫn do local GitHub automation (webhook handler) xử lý — sandbox chỉ dùng để chạy agent và lưu thay đổi trong phiên làm việc.

### Database path error

```
ValueError: Database path escapes allowed directories
```

→ Set `persistence.allow_any_path: true` hoặc dùng path trong `~/.k41-agent/`

## See Also

- [config.sample.yaml](../config.sample.yaml) - Sample configuration file
- [Architecture](./architecture.md) - System architecture overview
