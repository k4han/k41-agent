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
| Server log | `~/.k41-agent/server.log` |
| Bootstrap configuration | `~/.k41-agent/config.yaml` |

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
