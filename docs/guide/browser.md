---
description: Use local Chrome for browser automation, watch the session, and take control when a task needs your input.
---

# Browser automation

K41 can work with JavaScript websites using a local Chrome browser. Browser
automation uses the K41 host, including when your project workspace runs in an
optional remote execution environment.

## Set up the browser

1. Open **Settings > Browser** to view browser status, install Chrome/Chromium, or check
   an existing installation. Automatic installation supports Debian/Ubuntu x86_64 and ARM64;
   on other platforms, install Chrome/Chromium with your system installer.
2. Open **Settings > Agents** and select the intended agent.
3. Enable its Browser tools under **Capabilities & Tools**.
4. Start a dashboard conversation with that agent.

If automatic browser discovery fails, set `browser.executable_path` in
**Settings > Browser**.

The Browser settings page also manages browser storage, idle timeout, and
operation timeout. Install, reinstall, and check actions run in the background;
you can leave the page and return to see the result and operation output. Checks
use a temporary profile and do not change your saved browser logins. If you set
a custom executable path, save it before checking. A successful installation
also verifies that custom path.

Dashboard installation requires root or passwordless sudo access for the server
process. It never prompts for a sudo password in the dashboard. When the server
lacks permission, use the CLI from a terminal with the appropriate privileges.

Run `k41 browser check` to test headless startup and browser automation with a
temporary profile. If you configured a custom path, check that same binary with
`k41 browser check --executable-path /path/to/chrome`. On Debian/Ubuntu x86_64 or ARM64,
`k41 browser install --force` reinstalls Chrome/Chromium and repairs system dependencies.
Package installation requires root or cached sudo credentials (`sudo -v`);
run the check as the same user that runs the agent. See
[installation](./installation#optional-browser-support) to include browser setup
in the shell installer.

Both CLI and dashboard installation complete pending `dpkg` configuration before
downloading Chrome. Package setup runs without prompts and preserves existing
configuration files when no default answer is available. If this step fails,
run `sudo dpkg --configure -a` in a terminal on the host, resolve the reported
package errors, and retry installation. The dashboard server still needs its own
root or passwordless sudo access; `sudo -v` in a terminal does not grant that
access to a background server process.

A source installation needs its normal `uv sync` dependencies. No Browserbase
account or separately configured browser model is required.

On ARM64, installation uses Playwright's full Chromium build and installs its
system libraries with `apt-get`. The browser is downloaded as the agent user;
only package installation uses sudo. K41 discovers it through
`~/.k41-agent/browser/chromium`, including after restarting the service. This
avoids Snap restrictions on the profile and Stagehand extension directories.
Use a Debian/Ubuntu release supported by
[Playwright](https://playwright.dev/python/docs/intro#system-requirements).

## Ask for a browser task

For example:

> Open the project website and check whether the installation guide is easy to
> find from the home page.

Open the workspace's **Browser** tab to watch the session. An active browser
session also exposes a shortcut in the chat header.

Screenshots taken with `browser_screenshot` are saved in the active workspace
under `.k41-agent/browser/screenshots/`. The tool returns the image's path within
that workspace, and the image remains visible in the conversation.

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
