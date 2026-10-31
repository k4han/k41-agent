---
description: Install K41 Agent on Windows, macOS, or Linux and start the local dashboard.
---

# Installation

The installer downloads the latest release and prepares its Python environment
with uv. Choose the command for your operating system.

## Windows

Open **PowerShell** and run:

```powershell
irm https://k4han.github.io/k41-agent/install.ps1 | iex
```

You can [read the PowerShell installer](https://github.com/k4han/k41-agent/blob/main/install.ps1)
before running it.

## macOS and Linux

Open a terminal and run:

```sh
curl -fsSL https://k4han.github.io/k41-agent/install.sh | bash
```

If `curl` is not available:

```sh
wget -qO- https://k4han.github.io/k41-agent/install.sh | bash
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
For a fresh installation, the default admin password is `1234`, as shown on the
login page. Change it after the first login:

```sh
k41 reset-password
```

Next, [connect a model and start a conversation](./getting-started).

## Install a specific release

For example, to install `v0.1.35` on Windows:

```powershell
Invoke-WebRequest -Uri "https://k4han.github.io/k41-agent/install.ps1" -OutFile ".\install.ps1"
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ReleaseTag v0.1.35
```

On macOS or Linux:

```sh
curl -fsSL https://k4han.github.io/k41-agent/install.sh -o install.sh
chmod +x install.sh
./install.sh --release-tag v0.1.35
```

## Optional browser support

On Debian/Ubuntu x86_64, install and verify Chrome for
[browser automation](./browser) with:

Open **Settings > Browser** and select **Install Chrome** to set it up from the
dashboard. The server process needs root or passwordless sudo access. You can
also install it from a terminal:

```sh
k41 browser install
```

The command reuses an existing Chrome/Chromium installation. Otherwise it
downloads official Google Chrome and uses `apt-get` to install it with its system
dependencies. Run it as the agent's user; package installation requires root or
cached sudo credentials (`sudo -v`). It does not require a desktop session.

To include this step in the shell installer:

```sh
curl -fsSL https://k4han.github.io/k41-agent/install.sh | bash -s -- --with-browser
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
