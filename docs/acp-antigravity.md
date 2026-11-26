# ACP agents: Google Antigravity

K41 connects to Antigravity exclusively through `agy acp` over stdio JSON-RPC.
Agent cards use `graph_type: acp_agent` and the runtime identifier `antigravity-cli`.

## Setup

1. Open **Settings > ACP Agents** and choose **Download and install** for Antigravity.
   K41 also detects an `agy` executable already on `PATH`.
2. Run the displayed sign-in command on the machine running K41, then choose
   **Refresh** to check the version and account status.
3. Select the built-in **Antigravity** card in Chat, or create your own ACP card.

K41 uses the CLI's existing credentials and an explicit `GEMINI_HOME`. Each card
runs with a K41-managed profile for its prompt and native tool selection. The CLI
must be version 1.3.3 or newer and support `--agent` and `--process-per-session`.

The dashboard shows the current and latest available versions. **Update** and
**Uninstall** support K41 installations and binaries in Google's default installer
location. For a custom location, use its original installer. Uninstalling removes
the executable while preserving credentials, configuration and conversations.

~~~markdown
---
name: acp-reviewer
graph_type: acp_agent
permission_mode: ask
tools: [read, web_search]
acp:
  agent: antigravity-cli
  tools: null
---

Review the code and explain actionable findings.
~~~

Each process runs `agy acp --process-per-session`. Processes are isolated by
resolved workspace, native tool selection and prompt policy, and idle processes
are reaped.

## Prompt, tools and permissions

Configure the ACP card's system prompt, tools, tool settings, MCP bindings and permissions
directly. Card instructions and selected tools are sent when a new session starts.

The **ACP** category inside the card's **Tools** tab lists AGY's native tools.
They are separate from K41 tools, even when their names match. Select tools
individually or select/clear the category. New cards enable all optional native
tools by default. Required internal helpers such as `send_message` and
`manage_task` are shown as always enabled.

`acp.tools: null` (or an omitted `acp.tools`) enables all catalogued native tools.
An explicit list selects only those native tools; `acp.tools: []` disables all
optional native tools. Card-level `tools` continues to select K41 tools bridged
over MCP. Names are AGY profile component identifiers; their callable names can
vary by CLI version (for example, `view_file` supplies `read_file` in current AGY).
Retired no-op components are excluded from the catalog. Unknown native tool names
are rejected before starting AGY.

K41 creates its profile under its ACP data directory, discovers it with `--add-dir`,
and passes `--include-default-customizations=false`. The profile sets
`excludeDefaultComponents: true`, `inheritCustomizations: false`, `inheritMcp: false`
and the selected native tool names. Its Markdown body is empty: the card's visible
system prompt, with prompt variables resolved and tool-specific additions, is
sent once when the session starts. No placeholder instructions, AGY default prompt
or external profile instructions are added. Existing user and workspace profiles
remain unchanged. AGY may still expose internal task and MCP resource helpers.

~~~yaml
graph_type: acp_agent
permission_mode: ask
tools: [read, web_search, mcp__github__create_issue]
acp:
  agent: antigravity-cli
  tools: [view_file, search_web]
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

Chat choices are stored per card and workspace in the current browser. Legacy
card-level choices seed a CLI scope only when that scope has no saved choices.
Existing default-profile CLI choices, including an explicitly empty choice set,
take precedence. Values no longer advertised by the CLI are filtered out.

Channels and scheduled runs use card defaults unless an API client supplies
`acp_config_options`. The agent's `mode` option is excluded because K41 controls
approvals. The normal K41 provider/model and reasoning-effort fields do not apply.

## Existing configurations and sessions

Cards using the former `antigravity-acp` identifier, or a missing, null or blank
`acp.agent`, automatically load as `antigravity-cli`. Prompt, tools, MCP bindings,
permissions and configuration options are preserved. Reading a card does not rewrite
its file; saving or serializing it writes the canonical CLI identifier. Unknown runtime
identifiers are rejected.

The former `acp.use_default_tools` setting is read without rewriting the card:
`false` becomes `acp.tools: []`, while `true` or a missing flag uses the all-tools
default. An explicit `acp.tools` takes precedence. The former `acp.agent_name` is
ignored: the card's visible prompt is the only source of base instructions.
Saving writes only the new settings and preserves `config_options`.

Old server sessions and checkpoints without a CLI runtime key start a new CLI session
with a notice. Stored K41 conversation history remains available, but the CLI does not
receive the old server's context. Existing CLI sessions continue to resume after restart.
Old server credentials, installation files and session data are not copied or deleted.

Changing the workspace, native tool selection, card prompt, K41 tools or permissions
starts a fresh session. Sessions from the previous prompt policy also start fresh.
Changing only model or thinking options preserves the session.
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

- `GET /dashboard-api/acp/agents` returns the supported runtimes.
- `GET /dashboard-api/acp/agents/antigravity-cli`: accepts optional `cwd` and
  `refresh` query parameters. The deprecated `agent_name` parameter is accepted
  but does not select an external profile.

Add `check_updates=true` to either status endpoint to check the current release
source. Responses use `Cache-Control: no-store` and expose the installation source,
versions, authentication, configuration options and latest installation operation.
Refresh invalidates cached probes and restarts idle CLI processes, while active
turns keep running. The old runtime identifier returns 404.

To manage an installation, use `POST /dashboard-api/acp/agents/{id}/install`,
`POST /dashboard-api/acp/agents/{id}/update`, or `DELETE /dashboard-api/acp/agents/{id}`.
Send the current `installation_revision` as `If-Match` and poll the agent status
for the operation result. Busy agents and stale revisions return `409`.
