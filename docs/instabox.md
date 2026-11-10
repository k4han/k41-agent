# Instabox workspace backend

[Instabox](https://instabox.sh/docs/llms.txt) machines can run conversation and
project workspaces next to the Local, Daytona and Modal backends. An Instabox
machine is a full VM with persistent disk, so the backend also exposes machine
management, preview ports, a remote desktop, snapshots, restore and fork.

## Configuration

Enable the backend in **Settings → Providers → Execution environment → Instabox**
or set the runtime keys below. The API key is stored encrypted.

| Key | Default | Notes |
| --- | --- | --- |
| `workspace.instabox.enabled` | `false` | Turns the backend on. |
| `workspace.instabox.api_key` | — | `ibk_…` key. Never logged; masked in errors. |
| `workspace.instabox.api_url` | `https://api.instabox.sh` | Override for staging. |
| `workspace.instabox.workspace_root` | `/data/home/workspace` | Root for workspace files on the machine (persistent disk). |
| `workspace.instabox.template` | `agent-browser` | Template for new machines. Ignored when a snapshot is used. |
| `workspace.instabox.snapshot_id` | — | Create new workspace machines from this snapshot instead of the template. |
| `workspace.instabox.org_id` | — | Organisation for new machines. Empty uses the key's default. |
| `workspace.instabox.vcpus` / `mem_mib` | — | Machine size. Sent only when both are set. |
| `workspace.instabox.auto_suspend_after_seconds` | — | Idle suspend timer for new machines. |

Empty values are not sent, so Instabox defaults apply.

### API key scopes

The key needs:

- `machines:read`, `machines:write` — list, create, start/stop/suspend/resume,
  exec, files, ports, desktop, fork, restore and delete machines.
- `images:read`, `images:write`, `images:delete` — list, create and delete snapshots.

Machine images must provide `python3` 3.11 or newer and `git`. The workspace
backend checks both when it prepares a machine and reports a clear error otherwise.

## How it behaves

- **States.** `running` maps to `started`, `suspended` to `paused`, `stopped` to
  `stopped`, `deleted` (or `machine_not_found`) to `destroyed`, transitional states
  to `pending`. Network and authentication failures report `unknown` instead of
  marking a workspace as lost. Listing and status reads never wake a machine.
- **Durable operations.** Create, start, stop, suspend, resume, fork, restore and
  delete send an `Idempotency-Key`. The key is saved in `instabox_operations`
  before the request goes out, and unfinished records are resumed in the
  background at startup with the same key, so a crash cannot create a second
  machine. Progress is visible in the machine's **Operations** tab.
- **Commands.** `exec` is never retried automatically, because the first attempt
  may already have run. Long coding tool calls hold an activity lease so idle
  suspend does not interrupt them.
- **Ports.** Ports are published private by default. The access token is stored
  encrypted (`instabox_port_tokens`) and only added to the link when you open or
  copy it. If no token is stored, rotate it to get a new link. Making a port
  public deletes the stored token.
- **Desktop.** Agent desktop tools (`instabox_desktop_*`) run one per turn and
  are refused while a person holds control. In the dashboard you watch by
  default; **Take control** pauses the agent, heartbeats every 10 seconds and
  returns control on **Hand back** or after 30 seconds without a heartbeat. The
  browser connects through `/dashboard-api/instabox/desktop/viewer` with a
  one-time ticket (60 seconds); the server holds the API key.
- **Snapshots, restore and fork.** A snapshot must be `ready` before use.
  Restore asks for the machine ID and then the snapshot ID, refuses while
  operations, background tasks or workspace processes are running, saves every
  workspace on the machine first, then refreshes the saved copy from the
  restored disk. Agents always need user approval for
  `instabox_snapshot_restore`; machine and snapshot deletion ask by default.
  Fork copies a running machine; when Instabox answers `fork_unavailable`,
  create a snapshot and start a new machine from it instead.

## Dashboard API

All routes live under `/dashboard-api/instabox` and require an admin session:
`machines`, `templates`, `operations`, `machines/{id}/ports`, `snapshots`,
`machines/{id}/restore`, `machines/{id}/desktop/*`. Deleting a machine or a
snapshot requires a JSON body `{"confirm": "<id>"}`.

## Live test

```bash
INSTABOX_LIVE=1 INSTABOX_API_KEY=ibk_... uv run pytest tests/test_instabox_live.py
```

The test creates a machine, runs a command, publishes a private port, starts
the desktop and takes a screenshot, snapshots, forks, restores and finally
deletes everything it created. Optional: `INSTABOX_API_URL`, `INSTABOX_TEMPLATE`.
