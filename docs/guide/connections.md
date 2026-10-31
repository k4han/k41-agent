---
description: Connect MCP servers, GitHub repositories, and optional Telegram or Discord channels to K41 Agent.
---

# Connections and channels

Connections give the runtime access to external tools and services. Channels
provide another place to have a conversation with the agent. Configure only the
ones your workflow needs.

## MCP servers

Open **Settings > Connections > MCP Servers** to add a server.

For a local `stdio` server, configure its command, arguments, and any required
environment variables. For a supported HTTP server, configure its URL and
headers. Use the connection test to check whether K41 can reach the server and
discover its tools.

Then make the required MCP capabilities available to the intended
[agent](./agents). A connected server and an agent's enabled tools are separate
pieces of the setup.

::: tip Local command execution
A local MCP server starts on the K41 host. Its executable and any required
environment must be available there, even when the conversation uses a remote
workspace.
:::

## GitHub repositories

Open **Settings > Connections > Repositories** to configure GitHub access and
repository bindings supported by your setup.

Repository access in a workspace and GitHub webhook automation serve different
purposes. Webhook-based workflows also need the corresponding GitHub app,
webhook, and channel configuration.

For operator-managed environment settings, see the repository's
[environment example](https://github.com/k4han/k41-agent/blob/main/.env.example).

## Telegram and Discord

1. Open **Settings > Channels**.
2. Configure the bot credentials and the channel's agent settings.
3. Enable the channel after its required settings are in place.
4. Use the channel pairing flow when connecting a user identity.

Generate a root-user pairing code from the terminal with:

```sh
k41 pair-code
```

Channel integrations are optional. A basic local dashboard setup works without
Telegram, Discord, or GitHub webhooks. Source installations can add the relevant
optional dependencies through uv when needed.

## Browser work belongs in the dashboard

[Browser automation](./browser) requires the authenticated dashboard account
identity. Channel and scheduler runs do not provide that identity, so perform
browser tasks from a dashboard conversation.

## Diagnose a connection

Check the service credentials, the configured transport or endpoint, the runtime
status, and the selected agent's tools. A successful connection test does not
replace enabling the tools for the agent that will use them.
