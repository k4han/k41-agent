---
title: Command line
description: A reference for starting, stopping, updating, and managing the K41 Agent runtime.
---

# Command line

Run these commands in a new terminal after [installation](./installation).

## Runtime

| Command | Purpose |
| --- | --- |
| `k41` | Start the server in the background. |
| `k41 --foreground` | Run the server in the current terminal. |
| `k41 status` | Check server and dashboard status. |
| `k41 stop` | Stop the server. |
| `k41 stop --timeout 60` | Allow up to 60 seconds for the server process to exit. |
| `k41 restart` | Confirm shutdown, then start the server again. |
| `k41 restart --with-tray` | Restart both the server and system tray. |
| `k41 cli` | Open the terminal chat interface. |

The default dashboard is at
[http://127.0.0.1:4141](http://127.0.0.1:4141).

Stopping waits up to 30 seconds by default. If the process is still alive,
the command retains its PID file and exits with an error. `restart` does not
start another server when shutdown cannot be confirmed. For an unmanaged
server, neither command force-kills an unresponsive process. Use `--with-tray` to also stop or restart
the tray, or `restart --no-tray` to restart without a tray.

PID files may remain after a foreground run or an external shutdown. They
are removed by the controller after process exit is confirmed, or replaced
by a subsequent server start.

## Browser setup

| Command | Purpose |
| --- | --- |
| `k41 browser install` | Reuse Chrome/Chromium or install it with system dependencies on Debian/Ubuntu x86_64 or ARM64, then verify automation. |
| `k41 browser install --force` | Reinstall Chrome (x86_64) or Chromium (ARM64) and repair system dependencies on Debian/Ubuntu. |
| `k41 browser check` | Verify headless browser automation using a disposable profile. |
| `k41 browser check --executable-path /path/to/chrome` | Verify a specific Chrome/Chromium executable. |

Run these commands as the agent's user. Package installation requires root or
cached sudo credentials (`sudo -v`). Existing browser profiles are not changed.

## Account and channels

| Command | Purpose |
| --- | --- |
| `k41 reset-password` | Set a new dashboard admin password. |
| `k41 pair-code` | Create a root-user pairing code for a supported channel. |

See [connections and channels](./connections) for channel setup.

## Updates

```sh
k41 update --check
k41 update
```

The first command checks for a release. The second applies the update.
See [updates and maintenance](./maintenance) for backup and data behavior.

## Linux service

| Command | Purpose |
| --- | --- |
| `k41 service status` | Show the unit file and enabled or active state. |
| `k41 service restart` | Restart the background service. |
| `k41 service stop` | Stop the background service. |
| `k41 service disable --now` | Disable autostart and stop the service. |
| `k41 service install` | Reinstall and enable autostart. |

These commands manage the Linux systemd user service.
When that service is installed, `k41`, `k41 stop`, and `k41 restart` also use
systemd. Stop and restart wait for systemd's configured stop timeout, including
older units with a 90-second timeout, and verify the resulting unit state.
An unavailable systemd manager causes an error so a second unmanaged server
is not started. `k41 --foreground` continues to run in the current terminal.

## Running from a source checkout

Prefix the application command with `uv run`:

```sh
uv run k41 --foreground
```

Initialize a source installation with `uv run k41 init` after building the
dashboard. See [developing from source](./installation#developing-from-source).
