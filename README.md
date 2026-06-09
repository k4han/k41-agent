# K41 Agent

K41 Agent is an AI agent runtime that runs on your machine. You can use it through the web dashboard, the internal API, or additional channels such as Telegram, Discord, and GitHub.

The project is built with Python, FastAPI, LangGraph, and a Solid/Vite dashboard. Python and dependencies are managed with `uv`.

## What is it for?

- Chat with the agent through the dashboard.
- Select a workspace so the agent can read, search, and edit files.
- Manage LLM providers, models, MCP servers, skills, and agent profiles.
- Run background tasks, schedule automatic runs, and monitor runtime status.
- Connect Telegram, Discord, or GitHub to receive work from outside the dashboard.

## Quick Install on Windows

Open PowerShell in the project source directory, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

If PowerShell already allows script execution on your machine, you can use the shorter command:

```powershell
.\install.ps1
```

The installer will:

- Create the installation directory at `%LOCALAPPDATA%\k41-agent`.
- Download `uv` if the machine does not already have the K41 Agent private copy.
- Install Python 3.13 and dependencies from `uv.lock`.
- Copy the source into the runtime app directory.
- Run `k41 init` to create `~/.k41-agent/config.yaml` and the database.
- Create the `k41.cmd` command launcher in `%LOCALAPPDATA%\k41-agent\bin`.
- Add `%LOCALAPPDATA%\k41-agent\bin` to the user `PATH`.

After installation finishes, open a new terminal and set the admin password:

```powershell
k41 reset-password
```

Start K41 Agent:

```powershell
k41
```

The dashboard runs at this address by default:

```text
http://127.0.0.1:8000
```

Sign in with the admin password you just set.

## First-Time Setup

1. Go to `Settings > Providers`.
2. Add an LLM provider, API key, default model, and the list of models you want to use.
3. Go to `Settings > Agents` if you want to adjust the prompt, tools, or model for each agent.
4. Go to `Settings > Connections` to add an MCP server or GitHub repository.
5. Go to `Settings > Channels` if you want to enable Telegram, Discord, or GitHub webhooks.

If you only want to use the dashboard for chat or coding in a local workspace, you usually only need to configure a provider first.

## Common Commands

```powershell
k41                 # Run the server in the background
k41 --foreground    # Run the server in the foreground to view logs directly
k41 status          # Check the server, dashboard, API, and health endpoint
k41 stop            # Stop the server
k41 cli             # Open the chat CLI
k41 reset-password  # Reset the admin password
k41 pair-code       # Create an account pairing code for Telegram/Discord
```

Server logs are stored at:

```text
~/.k41-agent/server.log
```

Runtime data is stored here by default:

```text
~/.k41-agent/
```

## Update or Reinstall

Run the installer again from the new source:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer will stop the running app, copy the new source, sync dependencies, and keep the runtime data in `~/.k41-agent`.

## Uninstall

Uninstall the app while keeping runtime data:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

Uninstall the app and remove runtime data:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -RemoveRuntimeData
```

## Run from Source for Development

Install Python dependencies:

```powershell
uv sync
```

Install frontend dependencies:

```powershell
pnpm install
```

Initialize the local runtime:

```powershell
uv run k41 init
uv run k41 reset-password
```

Build the dashboard:

```powershell
pnpm dashboard:check
pnpm dashboard:build
```

Run the app from source:

```powershell
uv run python main.py
```

Or run the CLI entrypoint directly:

```powershell
uv run k41 --foreground
```

## Main Configuration

The bootstrap config file is located at:

```text
~/.k41-agent/config.yaml
```

Common values to edit in this file:

```yaml
host: "0.0.0.0"
port: 8000
enable_web: true
enable_api: true
enable_dashboard: true
```

Runtime settings such as LLM providers, API keys, MCP servers, channel tokens, GitHub private keys, timezone, and recursion limit are managed in the dashboard and saved to the database. Secrets are encrypted when stored.

## API

The API is available under `/api` and requires the same admin login session as the dashboard.

Some main endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/chat` | Synchronous chat |
| `POST` | `/api/chat/stream` | Text streaming chat |
| `POST` | `/api/chat/events` | Event streaming chat for the UI |
| `GET` | `/api/graphs` | View registered workflows |
| `GET` | `/api/providers` | View configured providers |
| `GET` | `/api/providers/models` | View model options |
| `GET` | `/health` | Check app status |

## Workflows and Agents

K41 Agent includes these default workflows:

| Workflow | Purpose |
| --- | --- |
| `react_agent` | Main agent that can call tools, read/edit files, run shell commands, and use skills |
| `research_chain` | Research and synthesis workflow |
| `router` | Classifies requests and routes them to the appropriate workflow |

Agent profiles are loaded from:

```text
~/.k41-agent/agents/
```

Examples and detailed guidance are available in:

- `agent/modules/agents/README.md`
- `agent/modules/agents/examples/`
- `docs/agent-quick-reference.md`

## Additional Documentation

- `docs/configuration.md`: detailed configuration.
- `docs/deployment-verification.md`: deployment verification checklist.
- `docs/graph-registration.md`: how to register workflows.
- `docs/refactor-agent-centric-api.md`: notes about the agent-centric API.
