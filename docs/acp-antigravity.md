# ACP agents: Google Antigravity

K41 connects to Antigravity exclusively through `agy acp` over stdio JSON-RPC.
Agent cards use `graph_type: acp_agent` and the runtime identifier `antigravity-cli`.

## Setup

1. Install Antigravity CLI on the machine running K41 and put `agy` on `PATH`.
2. Check `agy --version` and `agy acp --help`. The CLI must support `--agent`
   and `--process-per-session`. Update it with `agy update` when necessary.
3. Sign in on that machine with `agy acp --login`.
4. Open **Settings > ACP Agents** and choose **Refresh** to check the executable,
   version and account status.
5. Select the built-in **Antigravity** card in Chat, or create your own ACP card.

K41 uses the CLI's existing user and workspace configuration, including an explicit
`GEMINI_HOME`. Installation, updates and account management happen outside K41.
The dashboard provides read-only status; it has no installer, API-key form or sign-in relay.

~~~markdown
---
name: acp-reviewer
graph_type: acp_agent
permission_mode: ask
tools: [read, web_search]
acp:
  agent: antigravity-cli
  agent_name: reviewer
  use_default_tools: true
---

Review the code and explain actionable findings.
~~~

`agent_name` is optional and selects an existing AGY profile with `--agent`.
Leave it blank to use the AGY default. Workspace profiles are resolved from the
conversation's working directory. Names cannot start with a dash or contain control
characters and are limited to 128 characters.

Each process runs `agy acp --process-per-session`. Processes are isolated by
profile, resolved workspace and `use_default_tools`, and idle processes are reaped.

## Prompt, tools and permissions

Configure the ACP card's system prompt, tools, tool settings, MCP bindings and permissions
directly. Card instructions and selected tools are sent when a new session starts.

By default, `use_default_tools: true` preserves AGY's native tools and customizations.
To use the card's prompt and K41 tools without AGY's default file, command and web tools,
set `use_default_tools: false` and leave `agent_name` empty. This requires AGY 1.3.3
or newer. K41 creates its own profile under its ACP data directory, discovers it with
`--add-dir`, and passes `--include-default-customizations=false`.

The generated profile sets `excludeDefaultComponents: true`, `tools: []`,
`inheritCustomizations: false` and `inheritMcp: false`. Existing user and workspace
profiles remain unchanged. AGY may still expose internal task and MCP resource helpers.

~~~yaml
graph_type: acp_agent
permission_mode: ask
tools: [read, web_search, mcp__github__create_issue]
acp:
  agent: antigravity-cli
  use_default_tools: false
~~~

The built-in Antigravity card is read-only and has no K41 tools. Clone it or create
a custom card to select tools.

K41 supplies tools through a session-specific MCP endpoint on `127.0.0.1`, protected
by a random token. Tools marked `antigravity/alwaysLoad` are available directly.
Bridged tools run inside K41 and can use its configured connections.

Bridged K41 tools use the shared permission engine. ACP permission requests for the
`k41` MCP server receive allow-once; K41 checks the actual invocation arguments.
Conversation grants apply to exact operations and arguments. Native AGY tool requests
also pass through K41's ACP permission handling.

Use card-level `permission_mode`: `ask`, `auto`, `full_access`, or explicit `null`
to inherit the global setting. Deny rules and hard blocks remain enforced. Channels
and synchronous API runs report `approval_unavailable` when approval is required.

## Model and thinking options

After sign-in, the card and Chat composer list the model and thinking options reported
by the CLI. The order is the Chat choice, then the card's `acp.config_options`, then
the CLI's default. Choices apply from the next message, including in an existing session.

~~~yaml
acp:
  agent: antigravity-cli
  config_options:
    model: gemini-3.8-flash
    thinking_level: low
~~~

Chat choices are stored per card, profile and workspace in the current browser.
For the default profile, legacy card-level choices seed a CLI scope only when that
scope has no saved choices. Existing CLI choices, including an explicitly empty choice
set, take precedence. Values no longer advertised by the CLI are filtered out.

Channels and scheduled runs use card defaults unless an API client supplies
`acp_config_options`. The agent's `mode` option is excluded because K41 controls
approvals. The normal K41 provider/model and reasoning-effort fields do not apply.

## Existing configurations and sessions

Cards using the former `antigravity-acp` identifier, or a missing, null or blank
`acp.agent`, automatically load as `antigravity-cli`. Prompt, tools, MCP bindings,
permissions and configuration options are preserved. Reading a card does not rewrite
its file; saving or serializing it writes the canonical CLI identifier. Unknown runtime
identifiers are rejected.

Old server sessions and checkpoints without a CLI runtime key start a new CLI session
with a notice. Stored K41 conversation history remains available, but the CLI does not
receive the old server's context. Existing CLI sessions continue to resume after restart.
Old server credentials, installation files and session data are not copied or deleted.

Changing the profile, workspace, default-tool setting, card prompt, tools or permissions
starts a fresh session. Changing only model or thinking options preserves the session.
A turn interrupted by restart cannot resume: K41 reports it and never resends the prompt.
Actions already performed are not undone.

### Migrating legacy base cards

`acp.base_agent` has been removed. Migrate linked cards from the repository:

~~~powershell
uv run python -m scripts.migrate_acp_cards --card acp --card antigravity
~~~

Use `--card` once for each card and `--agents-dir` for a custom card directory.
The migration snapshots effective prompt, supported tools, settings and permissions,
and copies MCP bindings without duplicating server installations or credentials.
The source card remains independent. Files and binding metadata are backed up under
`.acp-card-backups`; failures restore the affected data. Rerunning is harmless.
Migrated cards serialize with the canonical CLI runtime identifier.

## Workflow limits and status API

ACP supports local workspaces. ACP cards cannot receive delegation or plan approvals.
The agent owns a linear session, so editing, forking, regeneration and conversation
compaction are unavailable. The first run fixes a conversation's workflow family;
in a chat channel, `/clear` releases it. Conversation titles can still use the
configured K41 provider.

The administrator-only endpoints are:

- `GET /dashboard-api/acp/agents`: returns the single CLI runtime.
- `GET /dashboard-api/acp/agents/antigravity-cli`: accepts optional `agent_name`,
  `cwd` and `refresh` query parameters.

Responses use `Cache-Control: no-store` and expose installation discovery,
authentication status, plan and selectable configuration options. Refresh invalidates
cached probes and restarts idle CLI processes, while active turns keep running.
The old runtime identifier returns 404. Installation, removal, sign-in, callback,
API-key and sign-out endpoints have been removed.
