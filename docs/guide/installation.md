---
description: Install K41 Agent on Windows, macOS, or Linux and start the local dashboard.
---

# Installation

The installer downloads the latest release and prepares its Python environment
with uv. Choose the command for your operating system.

## Windows

Open **PowerShell** and run:

```powershell
irm https://k41.bot.cd/install.ps1 | iex
```

You can [read the PowerShell installer](https://github.com/k4han/k41-agent/blob/main/install.ps1)
before running it.

## macOS and Linux

Open a terminal and run:

```sh
curl -fsSL https://k41.bot.cd/install.sh | bash
```

If `curl` is not available:

```sh
wget -qO- https://k41.bot.cd/install.sh | bash
```

You can [read the shell installer](https://github.com/k4han/k41-agent/blob/main/install.sh)
before running it. On Linux, the installer also attempts to enable a systemd user
service. See [maintenance](./maintenance#linux-autostart) for service commands.

## Start the dashboard

After installation, open a **new terminal** so your updated PATH is available:

```sh
k41
```

Open [http://127.0.0.1:4141](http://127.0.0.1:4141).
Sign in with the initial password `1234`. The console requires a new private
password before allowing dashboard or API access. Sign in again after saving it.

If you forget your password, reset it locally:

```sh
k41 reset-password
```

Fresh installations listen on `127.0.0.1`. Existing network listeners can start
before password setup; default-password sessions can only change the password
or log out.

Next, [connect a model and start a conversation](./getting-started).

## Install a specific release

For example, to install `v0.1.43` on Windows:

```powershell
Invoke-WebRequest -Uri "https://k41.bot.cd/install.ps1" -OutFile ".\install.ps1"
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ReleaseTag v0.1.43
```

On macOS or Linux:

```sh
curl -fsSL https://k41.bot.cd/install.sh -o install.sh
chmod +x install.sh
./install.sh --release-tag v0.1.43
```

## Optional browser support

On Debian/Ubuntu x86_64 or ARM64, install and verify Chrome/Chromium for
[browser automation](./browser) with:

Open **Settings > Browser** and select **Install browser** to set it up from the
dashboard. The server process needs root or passwordless sudo access. You can
also install it from a terminal:

```sh
k41 browser install
```

The command reuses an existing Chrome/Chromium installation. Otherwise it
downloads official Google Chrome on x86_64 or Playwright's full Chromium build on
ARM64, and uses `apt-get` to install the system dependencies. The ARM64 path
requires a [Playwright-supported Debian/Ubuntu release](https://playwright.dev/python/docs/intro#system-requirements).
Run it as the agent's user; package installation requires root or
cached sudo credentials (`sudo -v`). It does not require a desktop session.

To include this step in the shell installer:

```sh
curl -fsSL https://k41.bot.cd/install.sh | bash -s -- --with-browser
```

`K41_AGENT_WITH_BROWSER=true` also enables this step. Browser setup failures stop
the installer before autostart, so it never reports a successful browser setup
when verification fails. Browser support is optional for basic chat.

On other platforms, install Chrome or Chromium using the system installer, then
run `k41 browser check` to verify it. From a source checkout, prefix these commands
with `uv run`.

## Developing from source

From a clone of the repository:

```sh
uv sync
pnpm install
pnpm dashboard:check
pnpm dashboard:build
uv run k41 init
uv run k41 --foreground
```

Build the dashboard before running the repository's installer from a clone.
The generated dashboard assets are not tracked in Git.
