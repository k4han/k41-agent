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

Open **Settings > Browser > Login profiles** to create, rename, and delete saved
profiles, choose an optional account default, and see projects using a profile
and the conversation holding it. Profiles belong to your account and can serve
several projects. Deleting a profile removes its saved logins everywhere.

In a project's **Browser** tab, choose **Use account default**, a specific profile,
or **Separate login for each conversation**. Projects without an explicit choice
keep separate logins, including existing projects after an account default is
set. Conversations outside projects use the account default, if selected.

A conversation takes this choice once, when its browser binding is created,
even if the conversation started earlier. Changing the account default or a
project's choice does not change existing bindings. Deleting their selected
profile resets them to separate logins; it does not switch them to another
account's sign-ins. An inheriting project continues to follow the account default
for conversations without bindings.

To choose for a single conversation, open **Login profiles** in the Browser
toolbar. Close the browser before changing profiles. **This conversation only**
keeps that conversation's sign-ins separate, even when its project has a profile.
Profile management is available through **Manage login profiles** in Settings.

A named profile can be open in only one browser at a time. A sub-agent uses its
parent conversation's profile if that browser is closed, and returns the profile
when its task ends. If the browser is open, the sub-agent uses a separate profile
without those sign-ins, so close the browser first when a sub-agent needs them.

Any agent with Browser tools in a conversation using a profile can act with its
sign-ins, and a malicious page could try to direct that agent. Keep important
accounts in profiles that only the projects needing them can use.

Profiles belong to the authenticated dashboard account. Use a dashboard
conversation for browser tasks; channel and scheduler runs do not supply that
account identity.

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
