# K41 Agent

K41 Agent là một AI agent runtime chạy trên máy của bạn. Bạn có thể dùng qua dashboard web, API nội bộ, hoặc kết nối thêm các kênh như Telegram, Discord và GitHub.

Dự án được xây bằng Python, FastAPI, LangGraph và dashboard Solid/Vite. Python/dependency được quản lý bằng `uv`.

## Dùng để làm gì?

- Trò chuyện với agent qua dashboard.
- Chọn workspace để agent đọc, tìm kiếm và chỉnh sửa file.
- Quản lý LLM provider, model, MCP server, skills và agent profile.
- Chạy task nền, lịch chạy tự động và theo dõi trạng thái runtime.
- Kết nối Telegram, Discord hoặc GitHub để nhận việc từ bên ngoài dashboard.

## Cài nhanh trên Windows

Mở PowerShell tại thư mục source của dự án, rồi chạy:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Nếu PowerShell trên máy đã cho phép chạy script, có thể dùng lệnh ngắn hơn:

```powershell
.\install.ps1
```

Script cài đặt sẽ:

- Tạo thư mục cài đặt tại `%LOCALAPPDATA%\k41-agent`.
- Tải `uv` nếu máy chưa có bản dùng riêng cho K41 Agent.
- Cài Python 3.13 và dependency theo `uv.lock`.
- Copy source vào thư mục app runtime.
- Chạy `k41 init` để tạo `~/.k41-agent/config.yaml` và database.
- Thêm `%LOCALAPPDATA%\k41-agent\bin` vào user `PATH`.

Sau khi cài xong, mở một terminal mới và đặt mật khẩu admin:

```powershell
k41 reset-password
```

Khởi động K41 Agent:

```powershell
k41
```

Dashboard mặc định chạy tại:

```text
http://127.0.0.1:8000
```

Đăng nhập bằng mật khẩu admin vừa đặt.

## Thiết lập lần đầu

1. Vào `Settings > Providers`.
2. Thêm LLM provider, API key, default model và danh sách model cần dùng.
3. Vào `Settings > Agents` nếu muốn chỉnh prompt, tool hoặc model cho từng agent.
4. Vào `Settings > Connections` để thêm MCP server hoặc GitHub repository.
5. Vào `Settings > Channels` nếu muốn bật Telegram, Discord hoặc GitHub webhook.

Nếu chỉ muốn dùng dashboard để chat/coding trong workspace local, bạn thường chỉ cần cấu hình provider trước.

## Lệnh thường dùng

```powershell
k41                 # Chạy server nền
k41 --foreground    # Chạy server ở foreground để xem log trực tiếp
k41 status          # Kiểm tra server, dashboard, API và health endpoint
k41 stop            # Dừng server
k41 cli             # Mở chat CLI
k41 reset-password  # Đặt lại mật khẩu admin
k41 pair-code       # Tạo mã ghép tài khoản cho Telegram/Discord
```

Log server nằm tại:

```text
~/.k41-agent/server.log
```

Dữ liệu runtime mặc định nằm tại:

```text
~/.k41-agent/
```

## Cập nhật hoặc cài lại

Chạy lại installer từ source mới:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Installer sẽ dừng app đang chạy, copy source mới, sync dependency và giữ lại dữ liệu runtime trong `~/.k41-agent`.

## Gỡ cài đặt

Gỡ app nhưng giữ dữ liệu runtime:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

Gỡ app và xóa cả dữ liệu runtime:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -RemoveRuntimeData
```

## Chạy từ source để phát triển

Cài dependency Python:

```powershell
uv sync
```

Cài dependency frontend:

```powershell
pnpm install
```

Khởi tạo runtime local:

```powershell
uv run k41 init
uv run k41 reset-password
```

Build dashboard:

```powershell
pnpm dashboard:check
pnpm dashboard:build
```

Chạy app từ source:

```powershell
uv run python main.py
```

Hoặc chạy CLI entrypoint trực tiếp:

```powershell
uv run k41 --foreground
```

## Cấu hình chính

File bootstrap config nằm tại:

```text
~/.k41-agent/config.yaml
```

Các giá trị thường chỉnh trong file này:

```yaml
host: "0.0.0.0"
port: 8000
enable_web: true
enable_api: true
enable_dashboard: true
```

Các cấu hình runtime như LLM provider, API key, MCP server, channel token, GitHub private key, timezone và recursion limit được quản lý trong dashboard và lưu vào database. Secret được mã hóa khi lưu.

## API

API nằm dưới `/api` và yêu cầu phiên đăng nhập admin giống dashboard.

Một số endpoint chính:

| Method | Endpoint | Mục đích |
| --- | --- | --- |
| `POST` | `/api/chat` | Chat đồng bộ |
| `POST` | `/api/chat/stream` | Chat streaming dạng text |
| `POST` | `/api/chat/events` | Chat streaming dạng event cho UI |
| `GET` | `/api/graphs` | Xem workflow đã đăng ký |
| `GET` | `/api/providers` | Xem provider đã cấu hình |
| `GET` | `/api/providers/models` | Xem model options |
| `GET` | `/health` | Kiểm tra trạng thái app |

## Workflows và agent

K41 Agent có các workflow mặc định:

| Workflow | Mục đích |
| --- | --- |
| `react_agent` | Agent chính có thể gọi tool, đọc/sửa file, chạy shell và dùng skill |
| `research_chain` | Workflow nghiên cứu và tổng hợp |
| `router` | Phân loại yêu cầu rồi chuyển sang workflow phù hợp |

Agent profile được load từ:

```text
~/.k41-agent/agents/
```

Ví dụ và hướng dẫn chi tiết nằm trong:

- `agent/modules/agents/README.md`
- `agent/modules/agents/examples/`
- `docs/agent-quick-reference.md`

## Tài liệu thêm

- `docs/configuration.md`: cấu hình chi tiết.
- `docs/deployment-verification.md`: checklist kiểm tra triển khai.
- `docs/graph-registration.md`: cách đăng ký workflow.
- `docs/refactor-agent-centric-api.md`: ghi chú API theo hướng agent-centric.
