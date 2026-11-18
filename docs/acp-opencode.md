# ACP agents: OpenCode

K41 connects to OpenCode through `opencode acp` over stdio JSON-RPC.
Agent cards use `graph_type: acp_agent` and the runtime identifier `opencode`.

## Setup

1. Install OpenCode CLI on the machine running K41 and put `opencode` on `PATH`
   (e.g., via `npm i -g @opencode/cli@latest` or package manager).
2. Check `opencode --version` and `opencode acp --help`.
3. Sign in or authenticate providers on that machine with `opencode auth login`.
4. Open **Settings > ACP Agents** and choose **Refresh** to check the executable,
   version, and status.
5. Select the built-in **OpenCode** card in Chat, or create a custom ACP card with `acp.agent: opencode`.

Installation, updates, and provider credentials happen outside K41. The dashboard
provides read-only status.

~~~markdown
---
name: opencode-assistant
graph_type: acp_agent
permission_mode: ask
tools: [read, web_search]
acp:
  agent: opencode
---

Review the code and suggest improvements.
~~~

Processes run `opencode acp` in the conversation's workspace directory. Idle
processes are automatically reaped.

## Prompt, tools and permissions

Configure the ACP card's system prompt, tools, MCP bindings, and permissions directly.
Card instructions are sent when a new session starts (wrapped in `<system_instructions>`
if custom system prompt append extensions are not available).

OpenCode provides its own built-in coding toolset. Any K41 tools and configured MCP
servers selected in the card are bridged to OpenCode via MCP endpoints passed during
`session/new`.

Permission handling follows K41's standard ACP permission approval workflow (`ask`, `auto`,
`full_access`).

## Model and configuration options

After connection, the card and Chat composer list the model and option settings reported
by OpenCode via ACP `configOptions`.
~~~yaml
acp:
  agent: opencode
  config_options:
    model: "claude-3-5-sonnet"
~~~
