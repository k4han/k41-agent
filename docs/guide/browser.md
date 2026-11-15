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

When a sub-agent browses, use the **Session** selector in **Browser** to choose the
main agent or an active sub-agent. New sessions keep your current selection;
when the selected session ends, the viewer switches to another active session.
Sub-agent sessions support live viewing and tab selection. Healthy sessions stay
open for the idle timeout after work finishes, including ordinary failures.
The viewer shows the session's state and remaining time. Use **Close browser**
to close the selected session. A browser handoff selects the requesting agent's session for you.
Asynchronous sub-agents can hand over their browser and wait while the main agent
continues working. Return control in Browser to resume that sub-agent. Its task
card also has an **Open browser** shortcut. Synchronous sub-agents report manual
interaction requests to their parent instead.

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

If the handoff times out, the browser closes automatically. The Browser tab
keeps a timeout notice with **Continue after timeout** and **Cancel browser task**,
including after reloading the conversation. Choose Continue to notify the agent
that the manual step was not confirmed, or Cancel to stop the task. The agent
asks whether to restart before repeating a timed out browser task.

## Reuse a login profile

Sign-ins stay in the profile after the browser closes, including sign-ins a site
keeps only for the browser session. A site can still end a session on its own
schedule. A sign-in made just before a crash may be lost, because Chrome writes
cookies to disk in batches.

Every account starts with a **Default** profile. Open **Settings > Browser > Login
profiles** to create custom profiles, choose the account default for projects,
and select an independent profile for temporary conversations. Both initially
use Default. The built-in profile cannot be renamed or deleted. The list shows
projects and all conversations using each profile.

In a project's **Browser** tab, choose **Use account default** or a specific saved
profile. Projects without an explicit choice inherit the account default.
Conversations outside projects use the temporary-conversation setting, which
stays independent when the account default changes.

Changes take effect when an inherited conversation's browser next opens.
An open browser retains its current profile. Deleting an unused custom profile
returns its references to inheritance or Default. Upgrades retain existing
named-profile choices as conversation overrides and preserve old login files.

To choose for a single conversation, open **Login profiles** in the Browser
toolbar. Close the browser before changing profiles. Select a saved profile to
override the setting, or return to the project or temporary-conversation setting.
Profile management is available through **Manage login profiles** in Settings.

Conversations using the same profile share one browser and its saved logins,
with their own tabs and human control. A sub-agent shares its parent's profile
and releases only its own tabs when it finishes. Closing one conversation leaves
the others open. A fatal browser failure or operation timeout ends all sessions
sharing that physical browser.

Scheduled runs retain healthy browser tabs after completion or ordinary failure,
using the same idle timeout as dashboard chats and sub-agents (10 minutes by default).
Cancelling a task closes its tabs immediately. Saved logins and screenshots remain
available after the browser closes. Human handoffs have a separate timeout
(30 minutes by default), which is not extended by viewing or typing.

Browser settings limit sessions per account and tabs per session (16 each by
default). When the session limit is reached, the oldest idle session closes first.
Running tasks and pending human handoffs are protected; opening another session
reports a capacity error if every slot is protected.

Any agent with Browser tools in a conversation using a profile can act with its
sign-ins, and a malicious page could try to direct that agent. Keep important
accounts in profiles that only the projects needing them can use.

Profiles belong to the authenticated dashboard account. Scheduled tasks created
by a dashboard admin use that account's browser profiles. Channel runs without
a dashboard account cannot use browser tools. Scheduled runs can pause for a
human handoff. Open the run's conversation to interact, then return control to
resume the same execution. Waiting survives application restart; if the browser
was lost, continuing reports the lost session rather than repeating the task.
Further ticks of the same job are recorded as skipped while a run is active or
waiting. Cancel an active task from scheduler run history when it is no longer needed.

## Sign in to sites that reject automated browsers

Google and some other sites refuse to sign in from a browser they identify as
automated, for example with "This browser or app may not be secure". K41 runs
Chrome headless, with no window or desktop required, and reports a regular Chrome
user agent so these checks do not reject it for being headless.

Sign in yourself through the Browser tab with a
[handoff](#take-control-when-needed). Sites can use other signals too, so this does
not guarantee that every site accepts the sign-in. Use an account you can recover
if a site restricts it.

## Understand host and workspace addresses

The browser runs on the K41 host. A URL such as `localhost` points to that host,
not automatically to a remote workspace. Remote applications need a preview URL
that the host browser can reach.

For session lifecycle, tool behavior, and the full configuration reference, see
the repository's [browser guide](https://github.com/k4han/k41-agent/blob/main/docs/browser-use.md).
