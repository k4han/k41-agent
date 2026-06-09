# K41 Agent

K41 Agent is a local AI workspace assistant with a web dashboard. It can chat, work with files in selected workspaces, run background tasks, and connect to external channels such as Telegram, Discord, and GitHub.

Use it when you want a private agent runtime on your own machine instead of a hosted workspace.

## Highlights

- Web dashboard for chat, workspace selection, settings, and runtime monitoring.
- Local runtime data under your user profile.
- Provider settings for OpenAI-compatible and other supported LLM services.
- Optional channel integrations for Telegram, Discord, and GitHub.
- One-command install, update, and uninstall on Windows, macOS, and Linux.

## Install

The installer downloads the latest release package and prepares everything needed to run K41 Agent.

### Windows

Open PowerShell and run:

```powershell
irm https://k4han.github.io/k41-agent/install.ps1 | iex
```

To install a specific release:

```powershell
Invoke-WebRequest -Uri "https://k4han.github.io/k41-agent/install.ps1" -OutFile ".\install.ps1"
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ReleaseTag v0.1.1
```

### macOS or Linux

Open a terminal and run:

```sh
curl -fsSL https://k4han.github.io/k41-agent/install.sh | bash
```

If `curl` is not available:

```sh
wget -qO- https://k4han.github.io/k41-agent/install.sh | bash
```

To install a specific release:

```sh
curl -fsSL https://k4han.github.io/k41-agent/install.sh -o install.sh
chmod +x install.sh
./install.sh --release-tag v0.1.1
```

## Start

Open a new terminal after installation and run:

```sh
k41
```

Then open:

```text
http://127.0.0.1:8000
```

Sign in with the default admin password shown on the login page:

```text
1234
```

Change the password after the first login:

```sh
k41 reset-password
```

## First Setup

1. Open `Settings > Providers`.
2. Add your LLM provider, API key, default model, and enabled models.
3. Open `Settings > Agents` if you want to adjust agent behavior.
4. Open `Settings > Connections` if you want to add MCP servers or GitHub repositories.
5. Open `Settings > Channels` if you want to enable Telegram, Discord, or GitHub webhooks.

For a basic local setup, configuring a provider is usually enough.

## Daily Commands

```sh
k41                 # Start the server in the background
k41 --foreground    # Start the server in the current terminal
k41 status          # Check server and dashboard status
k41 stop            # Stop the server
k41 cli             # Open the chat CLI
k41 reset-password  # Reset the admin password
k41 pair-code       # Create a pairing code for Telegram or Discord
```

## Update

Run the installer again. Your configuration, database, and runtime data are kept.

Windows:

```powershell
irm https://k4han.github.io/k41-agent/install.ps1 | iex
```

macOS or Linux:

```sh
curl -fsSL https://k4han.github.io/k41-agent/install.sh | bash
```

## Uninstall

Uninstall keeps runtime data by default.

Windows:

```powershell
& "$env:LOCALAPPDATA\k41-agent\uninstall.cmd"
```

macOS or Linux:

```sh
"${XDG_DATA_HOME:-$HOME/.local/share}/k41-agent/uninstall.sh"
```

Remove runtime data too:

```powershell
& "$env:LOCALAPPDATA\k41-agent\uninstall.cmd" --remove-runtime-data
```

```sh
"${XDG_DATA_HOME:-$HOME/.local/share}/k41-agent/uninstall.sh" --remove-runtime-data
```

## Installed Files

Windows:

```text
%LOCALAPPDATA%\k41-agent
```

macOS or Linux:

```text
${XDG_DATA_HOME:-~/.local/share}/k41-agent
```

Runtime data:

```text
~/.k41-agent
```

Server log:

```text
~/.k41-agent/server.log
```

## Troubleshooting

Check the running service:

```sh
k41 status
```

Stop and start again:

```sh
k41 stop
k41
```

View logs:

```text
~/.k41-agent/server.log
```

If the `k41` command is not found after installation, open a new terminal. On macOS or Linux, also make sure your shell profile has been reloaded.

## Development

Development installs use the local source tree. Build the dashboard before running `install.ps1` or `install.sh` from a clone, because `agent/delivery/http/dashboard/static/` is generated and is not tracked in git.

Install dependencies:

```sh
uv sync
pnpm install
```

Build the dashboard:

```sh
pnpm dashboard:check
pnpm dashboard:build
```

Initialize the local runtime:

```sh
uv run k41 init
```

Run from source:

```sh
uv run k41 --foreground
```

Install from the local source tree:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

```sh
./install.sh
```

## Documentation

- `docs/configuration.md`
- `docs/deployment-verification.md`
- `docs/agent-quick-reference.md`
