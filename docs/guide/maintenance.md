---
description: Update K41 Agent, manage Linux autostart, locate runtime data, and uninstall the application.
---

# Updates and maintenance

## Update your installation

To check for an available release:

```sh
k41 update --check
```

To apply the latest release:

```sh
k41 update
```

The updater checks GitHub releases, backs up the installed source, applies the
update, and restarts the server if it was running. Configuration, the database,
and runtime data are kept.

If an installation is damaged and the updater cannot run, use the
[installer](./installation) again. Reinstalls preserve runtime data and keep
backups of the previous application source.

## Linux autostart

On Linux, the installer attempts to create and enable the systemd user service
`k41-agent.service`.

```sh
k41 service status
k41 service restart
```

To allow the user service to start at boot without an interactive login:

```sh
sudo loginctl enable-linger "$USER"
```

The installer attempts this automatically. You can skip autostart during
installation by passing `--no-autostart` to the shell installer.

## Locate files and logs

| Data | Default location |
| --- | --- |
| Windows installation | `%LOCALAPPDATA%\k41-agent` |
| macOS or Linux installation | `${XDG_DATA_HOME:-~/.local/share}/k41-agent` |
| Runtime data | `~/.k41-agent` |
| Server log | `~/.k41-agent/logs/server.log` |
| Tray log | `~/.k41-agent/logs/tray.log` |
| Dashboard updater log | `~/.k41-agent/logs/update.log` |
| Bootstrap configuration | `~/.k41-agent/config.yaml` |

Logs rotate at 10 MiB per file, retaining up to five numbered backups per log.
Follow the active server log across rotations with
`tail -F ~/.k41-agent/logs/server.log`. Existing logs outside `logs/` are kept.
After upgrading, restart the server to use rotation. Refresh an existing Linux
service with `k41 service install --no-now` before restarting it.

Open **Settings > Logs** (`/settings/logs`) to inspect server, system tray, or
updater output. The viewer opens at the latest output and checks for new logs
every two seconds. Scroll up to load older output across numbered backups;
your reading position stays in place. While reading history, use **Latest logs**
or **New logs available** to return to the end. If rotation has removed the
history being viewed, return to the latest logs to continue.

A single `server.log` is expected until it reaches 10 MiB. To check an existing
Linux installation, inspect the file size and the installed service command:

```sh
ls -lh ~/.k41-agent/logs/server.log*
systemctl --user cat k41-agent.service
```

The service's `ExecStart` must invoke `agent.bootstrap.logs`; an older unit using
`StandardOutput=append:...` bypasses rotation. Refresh it with
`k41 service install --no-now`, then run `k41 service restart`. A directly started
`k41 --foreground --no-tray` writes to the terminal; use the managed service or
background mode for rotating file output. Files from older installations outside
`~/.k41-agent/logs/` are preserved and are not included in the viewer.

The bootstrap file and the dashboard's runtime settings serve different
purposes. Manage providers, agents, tools, and channels through their dashboard
settings pages.

On Linux, you can also inspect the service journal:

```sh
journalctl --user -u k41-agent -e
```

## Uninstall

Windows PowerShell:

```powershell
& "$env:LOCALAPPDATA\k41-agent\uninstall.cmd"
```

macOS or Linux:

```sh
"${XDG_DATA_HOME:-$HOME/.local/share}/k41-agent/uninstall.sh"
```

Uninstall keeps runtime data by default. Append `--remove-runtime-data` only
when you also intend to remove that data.
