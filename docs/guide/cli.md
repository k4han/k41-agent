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
| `k41 cli` | Open the terminal chat interface. |

The default dashboard is at
[http://127.0.0.1:4141](http://127.0.0.1:4141).

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

## Running from a source checkout

Prefix the application command with `uv run`:

```sh
uv run k41 --foreground
```

Initialize a source installation with `uv run k41 init` after building the
dashboard. See [developing from source](./installation#developing-from-source).
