# Configuration Guide

## Overview

Kai-agent sử dụng `~/.k41-agent/config.yaml` cho cấu hình bootstrap và dùng bảng `runtime_settings` trong DB cho cấu hình runtime quản trị qua dashboard. Tất cả settings vẫn được đọc tập trung qua ConfigService.

## Configuration File Location

```
~/.k41-agent/config.yaml
```

Trên Windows: `C:\Users\<username>\.k41-agent\config.yaml`
Trên Linux/Mac: `/home/<username>/.k41-agent/config.yaml`

## Environment File (.env)

Ngoài `config.yaml`, agent đọc file `.env` lúc khởi động để set biến môi trường (platform App identity, fallback cho tool credential, `K41_AGENT_HOME`, ...):

Platform-managed (operator-only, không nhập trong dashboard):

- `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY` hoặc `GITHUB_APP_PRIVATE_KEY_PATH`, `GITHUB_WEBHOOK_SECRET`
- `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`, `GOOGLE_CALENDAR_REDIRECT_URI`

1. `.env` trong thư mục working directory (nơi bạn chạy `k41` / `python main.py`)
2. `.env` trong agent home (`~/.k41-agent/.env`, đổi được qua `K41_AGENT_HOME`)

Biến đã tồn tại trong process environment **không** bị ghi đè, nên export từ shell/service manager luôn thắng `.env`. Xem [.env.example](../.env.example) cho danh sách biến.

## Configuration Structure

```yaml
# Server configuration
host: "0.0.0.0"
port: 4141

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
# Configure LLM providers, MCP servers, channels, tools, and recursion_limit
# from the dashboard. Channel settings live at Settings > Channels.
# Global tool credentials live at Settings > Tools.

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

### Optional: Tool Credentials (Settings > Tools)

### Named web connections

Manage web service credentials in `Settings > Providers > Search & Web`. The
provider area also contains Models, Execution Environments, and Decision Model
tabs. Routing strategy remains in Decisions & Routing; integrations and channels
keep their existing pages.

Google Search, Tavily, Firecrawl, Brave, and Bing support named connections. Create
a connection with a unique name, then optionally set it as the shared default for
that service. Tools can use the shared default or select a separate connection.
Agents inherit the tool selection, can select a named connection or the shared
default, and retain advanced credential overrides. DuckDuckGo and local fetching
do not require a connection. Image generation continues using LLM providers.

Connections are stored under `web.connections.<name>.*`, with shared defaults at
`web.defaults.<type>`. Tool bindings such as
`tools.web_search.tavily_connection` select a name; `__default__` selects the shared
default. The API key is encrypted at rest and never returned by the connection
API. Editing a connection without sending `api_key` preserves its key. Sending
an empty string or `null` clears the stored key.

Credentials resolve from nonempty agent overrides, then the selected connection,
then the existing service environment variables. An empty field can therefore
still use an environment credential. The dashboard shows whether configuration
is complete and which fields use the environment; this does not verify remote
connectivity. Invalid explicit connection names produce an error. Referenced
connections cannot be deleted until defaults, tools, and agents are updated.

On upgrade, after YAML settings have been seeded into the runtime database, web
credentials are migrated atomically once. Equal effective configurations become
a shared connection; different search/fetch configurations become separate named
connections. Existing new connections and bindings are preserved. Environment
secrets are never copied to the database. Original `tools.*` credential rows
remain for recovery but are ignored after migration. Legacy settings API writes
create or update a separate connection for that tool, preserving shared users.

Back up the runtime database and `data/runtime_config.key` before upgrading. To
roll back, stop the application, restore the pre-upgrade database and matching
encryption key, then run the previous version. Retained legacy rows describe the
pre-migration credentials and do not replace a complete pre-upgrade backup.

The following description of per-tool credential storage applies to legacy
configuration and compatibility input; new global credentials use named
connections as described above.

Cấu hình global cho built-in tool (ví dụ provider và credentials của `web_search`: Google Custom Search, Tavily, Firecrawl, Brave, Bing, DuckDuckGo; model mặc định của `generate_image`) nằm ở dashboard `Settings > Tools`.

- Key dạng `tools.<tool>.<field>` được lưu trong database (DB-owned) và mã hóa khi là secret.
- Nếu `config.yaml` có khai báo `tools.*`, các key này chỉ được copy một lần vào database khi runtime database source được attach. Sau đó mọi chỉnh sửa làm tại Settings > Tools (YAML không còn overlay).
- Gửi `null` (hoặc chuỗi rỗng) từ dashboard sẽ **xóa override đang lưu** và quay lại default của tool schema — không lưu giá trị null vào DB.
- Validate theo schema của tool: unknown tool/field, select option sai, number ngoài range đều trả HTTP 400.
- Per-agent override trong `Settings > Agents > Tools` hiển thị badge `Override`; placeholder/select option hiển thị giá trị global (`Global: ...` / `Global (set)` cho secret).
- Form cấu hình `web_search` tự động ẩn/hiện các trường credential linh hoạt theo provider đang chọn (ví dụ: chọn DuckDuckGo không cần key, chọn Tavily hiện Tavily API Key, chọn Firecrawl hiện Firecrawl API Key & Base URL, chọn Bing hiện Bing API Key, chọn Brave hiện Brave API Key, chọn Google hiện Google API Key & CSE ID, chọn Auto hiển thị đầy đủ để cấu hình theo thứ tự ưu tiên).

Fallback env (chỉ dùng khi global tool config để trống): `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `TAVILY_API_KEY`, `FIRECRAWL_API_KEY`, `FIRECRAWL_BASE_URL`, `BING_API_KEY`, `BRAVE_API_KEY` cho `web_search`.

## Configuration Precedence

Config được load theo thứ tự ưu tiên:

1. **Default values** (priority: 0) - Hardcoded defaults
2. **YAML file** (priority: 100) - `~/.k41-agent/config.yaml`
3. **Database** (priority: 200) - runtime settings quản trị qua dashboard

Higher priority overrides lower priority.

## Accessing Configuration

### From Code (DI via AppContainer, preferred)

```python
from agent.bootstrap.container import AppContainer

def my_handler(container: AppContainer):
    config = container.config_service

    # Typed getters
    host = config.get_str("host", "0.0.0.0")
    port = config.get_int("port", 4141)
    enabled = config.get_bool("enable_web", True)

    # Live settings, never snapshotted
    channel_enabled = container.runtime_settings.channel_enabled

    # Path with ~ expansion
    data_path = config.get_path("paths.data")
```

Legacy `get_config_service()` still resolves via the active container
for migration, but new code must receive `AppContainer` explicitly
(FastAPI `get_container(request)`, `CLIRuntime.container`, or
`isolated_container` fixture in tests).

### Nested Keys

Nested YAML structures được flatten thành dot-notation:

`security.jwt_secret` là secret nội bộ dùng để ký JWT admin. Nếu thiếu hoặc rỗng, app sẽ tự sinh và lưu vào `~/.k41-agent/config.yaml`; key này không được quản trị qua dashboard.

## Provider Backends

### Multi-provider (recommended)

Vào dashboard `Settings > Providers` để cấu hình nhiều provider.

Với `google`, trường `base_url` sẽ bị bỏ qua.

## Runtime Database Configuration

Các key `llm.*`, `mcp.servers.*`, `channels.*`, `tools.*` và `recursion_limit` được lưu trong DB. Dashboard ghi qua endpoint `/settings`; ConfigService đọc DB với priority cao hơn YAML.

`tools.*` là DB-owned: `YamlConfigSource` lọc các key này khỏi listings; chỉ có bước seed một lần khi attach database source mới copy từ `config.yaml` (nếu có) vào DB.

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

GitHub App V1 dùng một app cho toàn instance, không dùng OAuth từng user nên không cần `client_secret`. Cấu hình bắt buộc (platform-managed, operator-only):

Đặt trong server environment (self-host `.env`), không nhập trong dashboard:

```bash
GITHUB_APP_ID=
GITHUB_APP_SLUG=
GITHUB_APP_PRIVATE_KEY=
# hoặc GITHUB_APP_PRIVATE_KEY_PATH=/path/to/app.pem
GITHUB_WEBHOOK_SECRET=
```

Fallback operator YAML (cùng file `config.yaml`, self-host only) nếu không dùng env:

```yaml
channels:
  github:
    app_id: "123456"
    app_slug: "k41-agent"
    # private_key: "-----BEGIN RSA PRIVATE KEY-----\n..."
    # private_key_path: "/run/secrets/github-app.pem"
    # webhook_secret: "..."
```

#### Upgrade migration: credential cũ trong DB bị bỏ qua

Từ phiên bản platform-managed env, các key sau **không còn đọc từ database** (`runtime_settings`), dù hàng cũ vẫn nằm trong DB:

- `channels.github.app_id`, `channels.github.app_slug`, `channels.github.private_key`, `channels.github.private_key_path`, `channels.github.webhook_secret`
- `google_calendar.client_id`, `google_calendar.client_secret`, `google_calendar.redirect_uri`

Hệ quả: sau upgrade, `is_configured` có thể chuyển `False` dù DB còn giá trị. Lúc attach database source, app log warning liệt kê key bị bỏ qua, ví dụ:

```
Ignoring 2 legacy platform-managed setting(s) still stored in runtime_settings:
channels.github.private_key (GITHUB_APP_PRIVATE_KEY), ...
```

Operator cần copy thủ công một lần:

1. Đọc giá trị cũ từ DB (sqlite: `SELECT key, value_json FROM runtime_settings WHERE key LIKE 'channels.github.%' OR key LIKE 'google_calendar.%'`; postgres tương tự).
2. Ghi vào server environment (`.env` hoặc secret manager) hoặc operator `config.yaml` như mẫu trên. Env thắng YAML.
3. Xóa hàng orphan trong DB để tránh nhầm lẫn (sau khi đã verify `Test connection` chạy được).
4. Restart agent và bấm `Test` / `Sync` trong dashboard để verify.

Google Calendar tương tự:

```bash
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GOOGLE_CALENDAR_REDIRECT_URI=http://localhost:4141/integrations/google/callback
```

Trong dashboard Settings > Connections > GitHub, end user chỉ bật `channels.github.enabled`, chỉnh `default_agent`, `trigger_label`, `mention_triggers`, rồi bấm `Install App -> Sync` và cấu hình từng repository binding.

App cần quyền Metadata read, Issues read/write, Contents read/write, Pull requests read/write. Bật events `issues`, `issue_comment`, `pull_request_review_comment`, `installation`, `installation_repositories`, `ping`. Endpoint nhận webhook là `/channels/github/webhook`.

Repo được clone vào `<workspace.github.root>/{owner}/{repo}`. Khi issue/comment được trigger, backend chuẩn bị branch local, chạy agent trong working directory của repo, rồi commit/push/create PR bằng installation token. Khi có `pull_request_review_comment`, backend checkout branch đang mở PR, chạy agent với ngữ cảnh review comment, rồi commit/push lại cùng PR.

#### GitHub repo trong sandbox (Daytona / Modal)

Dashboard Workspace selector là UI 2 cấp:

1. **Backend**: `local` (host filesystem) | `daytona` | `modal`.
2. **Source**: tuỳ backend — `local` có `folder`, `daytona`/`modal` có `sandbox` và `github-repo`.

Khi chọn source `github-repo`, dashboard gọi `POST /dashboard-api/workspace/resolve` với `kind="github"`, `backend="local"|"daytona"|"modal"`, `repository_id=<id>`. Backend sẽ:

- Tạo (hoặc attach) sandbox tương ứng.
- Clone repo vào trong sandbox bằng `git clone --depth 1 --branch <default_branch> --single-branch` với installation token (nếu có). Vị trí: `{sandbox_root}/{owner}/{repo}`.
- Trả về `WorkspaceRef` với `metadata.source="github"`, `metadata.repository_full_name`, `metadata.repository_path`, và label là `owner/repo`.

Local backend clone repo về `<workspace.github.root>/{owner}/{repo}` và resolve qua `GitHubWorkspaceManager.ensure_shared_checkout`. Payload GitHub phải chỉ định `backend`; request thiếu backend sẽ bị từ chối.

Push về GitHub vẫn do local GitHub automation (webhook handler) xử lý — sandbox chỉ dùng để chạy agent và lưu thay đổi trong phiên làm việc.

### Database path error

```
ValueError: Database path escapes allowed directories
```

→ Set `persistence.allow_any_path: true` hoặc dùng path trong `~/.k41-agent/`

## See Also

- [config.sample.yaml](../config.sample.yaml) - Sample configuration file
- [Architecture](./architecture.md) - System architecture overview
