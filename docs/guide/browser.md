---
description: Use local Chrome for browser automation, watch the session, and take control when a task needs your input.
---

# Browser automation

K41 can work with JavaScript websites using a local Chrome browser. Browser
automation uses the K41 host, including when your project workspace runs in an
optional remote execution environment.

## Set up the browser

1. Install Chrome or Chromium on the K41 host.
2. Open **Settings > Agents** and select the intended agent.
3. Enable its Browser tools under **Capabilities & Tools**.
4. Start a dashboard conversation with that agent.

If automatic browser discovery fails, set `browser.executable_path` in
**Settings > Configuration**.

A source installation needs its normal `uv sync` dependencies. No Browserbase
account or separately configured browser model is required.

## Ask for a browser task

For example:

> Open the project website and check whether the installation guide is easy to
> find from the home page.

Open the workspace's **Browser** tab to watch the session. An active browser
session also exposes a shortcut in the chat header.

## Take control when needed

When you need to sign in, enter a verification code, or interact directly, ask
the agent to hand the browser over. During a handoff, the dashboard selects
Browser, shows the reason, and pauses the workflow for your input.

When finished, choose **Return control to agent**. The runtime captures a fresh
snapshot so the agent can continue from the current page.

Closing the Browser tab does not return control or close the session.
**Cancel browser task** ends the handoff and closes that browser.

## Reuse a login profile

Open **Login profiles** in the Browser toolbar to create or select a named
profile. Close the browser before changing profiles. A named profile can be
open in only one conversation at a time.

Profiles belong to the authenticated dashboard account. Use a dashboard
conversation for browser tasks; channel and scheduler runs do not supply that
account identity.

## Understand host and workspace addresses

The browser runs on the K41 host. A URL such as `localhost` points to that host,
not automatically to a remote workspace. Remote applications need a preview URL
that the host browser can reach.

For session lifecycle, tool behavior, and the full configuration reference, see
the repository's [browser guide](https://github.com/k4han/k41-agent/blob/main/docs/browser-use.md).
