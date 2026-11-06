# ACP agents: Google Antigravity

K41 can run Agent Client Protocol (ACP) agents as a workflow (`graph_type: acp_agent`). The first
supported agent is Google Antigravity, through the official `agy_acp_server` binary. The Antigravity
CLI is not required.

## Using it

1. Open **Settings > ACP Agents**, accept the vendor's terms, and choose **Install**. K41 reads the
   ACP Registry, accepts archives only from `dl.google.com`, and requires a matching sha256. Updates
   happen only when you choose **Check for updates**.
2. Sign in with Google, or enter a Gemini API key. If K41 runs on another machine, open the sign-in
   page in your own browser. It ends on a `127.0.0.1` address that cannot load: copy that address
   into **Final address**. The agent stops waiting 5 minutes after you start.
3. Pick the built-in **Antigravity** agent in Chat, or create a card with the workflow `acp_agent`:

```yaml
graph_type: acp_agent
acp:
  agent: antigravity-acp
  permission_mode: ask          # or auto_allow
  config_options:               # optional; choices are listed on the card once signed in
    thinking_level: low
```

### Choosing the model and thinking level

Once signed in, the Chat composer lists what the agent offers next to the agent name. The choice is
remembered per card in this browser and applies from the next message, including in a conversation
already under way. The order is: the choice in Chat, then the card's `config_options`, then the
agent's own default. Channels, the scheduler and other API clients use the card, unless an API client
sends `acp_config_options` itself.

The agent's `mode` option cannot be set this way, on a card or in a request: tool approvals come from
`permission_mode`.

Everything lives under `~/.k41-agent/acp/antigravity-acp`: versions, sign-in, and sessions.
**Remove** deletes all of it.

### Giving the agent K41 tools

The agent always has its own tools. To add K41 tools, select them under **Capabilities & Tools** on
an ACP card, or list them on the card. This covers built-in tools and the tools of MCP servers
enabled for the card:

```yaml
graph_type: acp_agent
tools: [web_search, web_fetch, mcp__github__create_issue]
acp:
  agent: antigravity-acp
```

The built-in **Antigravity** card has no K41 tools and is read-only. Clone it or create your own card
to use them.

- **Approval.** Every call goes through `permission_mode`, like the agent's own edits and commands.
  K41 also checks this itself: a tool runs only if K41 saw that exact call approved, so an agent that
  skips the question, or changes the arguments afterwards, is refused. **Allow in this conversation**
  is kept by K41, in memory, until K41 restarts. The agent itself only ever hears "allow once".
- **K41's credentials.** These tools run inside K41 and can use its connections, such as calendar or
  GitHub access, which the agent does not otherwise have. Select only what the card needs, because
  text the agent reads can try to make it use them.
- **Permission rules.** `tool_permissions` still apply. A rule set to `ask` is refused, because a tool
  cannot pause for approval on this path. Use `allow` or `deny` for these tools.
- **Browser handoff.** `browser_handoff` works as for other agents, in dashboard conversations: the
  turn pauses until you choose **Return control to agent** or **Cancel browser task**, and the agent
  then receives the result. Stopping the turn, or sending a new message, cancels the handoff and closes
  the browser. Stopping also records the turn's output and ends the paused run. If nobody answers before
  the handoff expires (`browser.idle_ttl_seconds`), the browser closes and the agent is told right away.
  Its reply appears once you dismiss the expired handoff. Elsewhere, such as channels, the call fails
  because nobody can take over the browser.
- **Not available:** `ask_user`, `delegate_task`, `plan_mode_respond`, `request_plan_review`, `skill`
  and `write_todos`. They need a K41 graph run, which an external agent does not have. Selecting them
  on a card has no effect. A small `read` tool limited to retained tool output is added, as for other
  agents, so long results stay reachable.
- **Changing the selection** applies from the next message, including in a conversation already under
  way.

K41 serves these tools from an MCP endpoint on `127.0.0.1` with a random port and one secret token per
agent session. The endpoint starts the first time a card with tools runs.

### What is different from other agents

- **Tool approvals.** With `permission_mode: ask`, edits, shell commands and K41 tools pause for
  approval on the dashboard. Surfaces where nobody can answer (channels, the scheduler, the
  synchronous API) deny automatically. `auto_allow` approves everything the agent asks for.
- **The agent's tools run on the machine running K41, with K41's access.** Approval only covers what
  the agent asks about, and an approved shell command can reach anything that user can.
- **Card settings that do not apply.** Skills, sub-agents and the K41 provider and model settings are
  unused. ACP cards cannot be delegated to or receive plan approvals.
- **History is linear.** The agent's session cannot rewind, so messages cannot be edited, forked or
  regenerated, and the conversation cannot be compacted. A conversation cannot switch between an ACP
  agent and another agent: its first run decides. In a chat channel, `/clear` releases the thread.
- **Local workspaces only.** Daytona and Modal workspaces are refused.
- **Restarts.** A session resumes after a restart, but a turn that was running cannot. K41 reports
  it and never sends the prompt again. Actions the turn already took stay in place.
- **Conversation titles** still come from your configured provider, if any, so the first message
  goes to that provider too.

## Verified behaviour of `agy_acp_server` 1.4.5

Everything below was observed on Windows x86_64 against the real binary (2026-11-05), unless it is
listed under "Not verified".

### Distribution
- The ACP Registry entry is `antigravity-acp`. Archives come from `dl.google.com` with no redirects
  and match the registry sha256.
- The Windows archive is about 100 MB and extracts to about 195 MB. It contains
  `agy_acp_server.exe` and `localharness_external.exe`.
- The license is proprietary, so K41 downloads the binary on demand and never bundles it.

### Handshake
- `initialize` takes about 5 s and `session/new` about 3-4 s.
- Advertised: `loadSession`, `sessionCapabilities.{list,additionalDirectories,resume}`,
  `auth.logout`, `promptCapabilities.{image,audio,embeddedContext}`,
  `_meta.antigravity.systemPrompt.append`.
- `authMethods`: `oauth-personal`, `oauth-business`, `gemini-api-key`, `agent-platform`.
- With no credentials, `session/new` fails with `-32000` "Authentication required".
- It works with no client `fs` or `terminal` capability.

### `GEMINI_HOME` isolation is required
- The server reads `<GEMINI_HOME>/config/hooks.json`. Hooks installed there by other applications
  apply to it too. When a hook cannot be spawned, every tool is denied with
  `denied by pre-tool hook: External hook '<name>' failed to execute`, and neither `tool_call` nor
  `session/request_permission` is sent.
- `GEMINI_HOME` relocates the whole tree: `antigravity-acp/acp_token.json`, `settings.json`,
  `conversations/`, `brain/`. K41 therefore points it at its own data directory.

### Sign-in
- `authenticate` with `oauth-personal` prints
  `Open the following link to authenticate the ACP server: <url>` to **stderr**. The URL has
  `redirect_uri=http://127.0.0.1:<random port>/` and a `state` parameter. A child process listens on
  that port.
- The server gives up after 300 s (`-32000`, `data.reason = onboarding_failed`).
- **The listener answers 200 "Authentication complete" for any callback, including a wrong `state`.**
  The flow then fails and clears stored credentials. A callback relay must validate `state` itself
  before forwarding, because there is no second attempt.
- The stderr log contains the OAuth `state`, so stderr must never be persisted or logged raw.
- A new process reuses stored credentials without calling `authenticate`.

### Sessions
- `session/resume` restores context after a process restart with no replay. This is what K41 uses.
- `session/load` also works, and replays history as `session/update` before responding.
- Two sessions can run prompts concurrently in one process.
- `session/set_config_option` returns the full option list. Options seen: `model`,
  `thinking_level`, `mode` (`default`, `auto_edit`, `yolo`). A bad value returns `-32602`.
- Config options last only as long as the process. `session/resume` in the same process keeps them,
  but after a restart the session returns to the agent's defaults. K41 compares the values reported
  on resume and sets them again before the next prompt.
- `thinking_level` takes effect: the legacy `models.currentModelId` changes with it, for example from
  `gemini-3.8-flash-high` to `gemini-3.8-flash-low`.
- `session/list` works, but its entries have no `updatedAt`.
- `session/new` also returns the legacy `modes` and `models` fields.
- `_meta: {antigravity: {systemPrompt: {append: <text>}}}` on `session/new` takes effect.

### Tool calls and permissions
- Reads inside the workspace run without asking. Edits and shell commands ask.
- Paths outside the session roots are denied by the server without asking, even after other tools
  were allowed. Shell commands can still reach anything the OS user can, once approved.
- `session/request_permission` can arrive before or after the matching `tool_call`.
- Options are `allow`, `deny`, and for shell commands `allow_always`, whose
  `_meta["agy.security.warning"]` carries a warning message to show.
- On denial the tool is reported `failed` with `rawOutput: "Rejected by user"`.
- An approved edit runs under a **second** tool call id (`<sessionId>:<n>`). The original pending
  call is reported `failed` with `Tool call was approved but never executed.` at the end of the
  turn, which is noise rather than a failure.
- Results are in `rawOutput` (a string or an object). `content` is only set on some calls.
- Thought tokens are counted, but no `agent_thought_chunk` was observed.

### Client-provided MCP servers
- `initialize` advertises `mcpCapabilities: {http: true, sse: true}`. Both `http` and stdio servers
  work. K41 uses `http`, because its tools have to run inside the K41 process.
- The server connects on the first prompt, not on `session/new`. It sends `server/discover`,
  `initialize`, `notifications/initialized` and `tools/list`, and accepts a 405 on GET.
- Tools are named `<server>_<tool>` in titles, for example `k41_get_current_time`.
- Every call asks through `session/request_permission`, with `allow_always`, `allow` and `deny`.
  `kind` is `other` and `_meta` has `{mcp: {tool, server}, is_mcp_tool_call: true}`.
- `rawInput` holds the arguments twice: `{arguments: {...}, ...each argument}`.
- **`rawOutput` is not the result.** It reads `Call <tool> tool`, while the model does receive the real
  output. K41 therefore shows what the tool returned, as recorded by its own endpoint.
- `session/resume` on a live session with a changed `mcpServers` list takes effect: the next prompt
  connects with the new headers and lists the tools again. K41 uses this when a card's tools change.

### Cancellation and process lifetime
- `session/cancel` returns `stopReason: "cancelled"` in about 0.2 s, even with a permission request
  left unanswered. A trailing chunk `The request was cancelled by the client.` is sent.
- The session stays usable after a cancel.
- The process tree is `agy_acp_server.exe` -> `agy_acp_server.exe` -> `localharness_external.exe`.
- Closing stdin does **not** end the server, and killing only the root leaves orphans. K41 runs it
  in a kill-on-close job object on Windows and a process group elsewhere.

### Not verified
- `gemini-api-key` end to end (no key was available). The server's error text says it requires the
  `GEMINI_API_KEY` variable.
- A real remote sign-in. Browser sign-in on the same machine works through K41. The relay replays the
  same GET the browser sends and is tested against a fake listener, but not against Google's flow.
- `stopReason` on quota exhaustion. A third-party source reports a non-spec `"error"` value, so K41
  accepts unknown stop reasons.
- Linux and macOS, including the meaning of the Linux `--uid=` argument from the registry.
- How long the server waits for one MCP tool call. `browser_handoff` keeps its request open until the
  person answers or the handoff expires, which can take minutes.
