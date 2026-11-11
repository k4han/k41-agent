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
| `workspace.instabox.max_machines_per_thread` | `2` | Auxiliary machines an agent may keep per conversation. `0` forbids agents from creating machines. |

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
- **Desktop.** Agents use the shared `computer_screenshot` and `computer_action`
  tools; see [computer-use.md](computer-use.md). In the dashboard you watch by
  default; **Take control** pauses the agent, heartbeats every 10 seconds and
  returns control on **Hand back** or after 30 seconds without a heartbeat. The
  Instabox control lock (`takeover`/`handback`) is mirrored, but agent input is
  refused by the application itself because `xdotool` ignores that lock.
- **Snapshots, restore and fork.** A snapshot must be `ready` before use.
  Restore asks for the machine ID and then the snapshot ID, refuses while
  operations, background tasks or workspace processes are running, saves every
  workspace on the machine first, then refreshes the saved copy from the
  restored disk. Agents always need user approval for
  `instabox_snapshot_restore`; machine and snapshot deletion ask by default.
  Fork copies a running machine; when Instabox answers `fork_unavailable`,
  create a snapshot and start a new machine from it instead.

## Agent tools

Agents use Instabox in two ways.

1. **The workspace runs on a machine.** The user picks Instabox for the
   conversation or project. The usual coding tools (`bash`, `read`, `write`,
   `edit`, `grep`, …) run on that machine; no Instabox tool is needed.
2. **The agent creates auxiliary machines.** `instabox_machine_create` and
   `instabox_machine_fork` create a machine that belongs to the conversation.
   Ownership is recorded in the `thread_machines` table under the root
   conversation, so sub-agents share the machines of their parent. Each new
   machine is prepared for coding tools (Python 3.11+, Git, workspace root); the
   result says whether that worked.

### Scope

Every tool that takes a machine accepts only the machine of the current
workspace and the auxiliary machines of the conversation. Others are refused.

- `instabox_machine_list` lists only those machines.
- `instabox_machine_delete` deletes auxiliary machines only; the workspace
  machine is deleted from the dashboard.
- Snapshot tools accept snapshots created in the conversation and the configured
  `workspace.instabox.snapshot_id`. Snapshots made from the dashboard are not
  visible to agents.

### Working on an auxiliary machine

Agents that have at least one machine tool (`instabox_machine_*`) or
`copy_files` see an optional `target` argument on the coding tools. Pass the
machine ID to run there; omit it for the main workspace. Other agents keep the
plain schema.

- Auxiliary machines run Linux with a POSIX shell. Paths are relative to the
  machine's root.
- Processes and retained output paths live on the machine that produced them,
  so `read_process_output`, `stop_process` and `read` need the same `target`.
- Auxiliary data is temporary: it is not captured on the host, not part of a
  project and gets no project secrets or skills. Copy results back with
  `copy_files` before deleting the machine.
- `copy_files` copies a file or directory between the main workspace and an
  auxiliary machine, or between two auxiliary machines (64 MiB per call;
  `.k41-agent/` is skipped). Writes into the main workspace are saved like any
  other edit. Path permissions apply to every copied file. Links cannot escape
  the machine's root, and directory copies skip symbolic links and junctions.

Pending machine creates and forks keep their reserved slot when a tool call is
cancelled or times out. Their final result records ownership or releases the
slot, including when operation tracking resumes after an application restart.

Deleting a conversation detaches its auxiliary machines without deleting them.
They then appear as unattached on the **Sandboxes** page; delete them there.

### Desktop

The desktop is driven by the backend-neutral computer tools described in
[computer-use.md](computer-use.md). `InstaboxComputer` provides the primitives:
it starts (and installs) the desktop when needed, runs `xdotool` on display
`$DESKTOP_DISPLAY` and reads screenshots with `import`, `scrot` or `xwd`.
Agent configurations that still list `instabox_desktop_*` tools get the new
tools automatically, but permission rules written for the old names must be
renamed.

### Default permissions

| Action | Default |
| --- | --- |
| `instabox_machine_create`, `instabox_machine_fork` | ask |
| `instabox_port_public` (checked when `access` is `public`) | ask |
| `computer_open_url`, `computer_type_secret` | ask (see [computer-use.md](computer-use.md)) |
| `instabox_machine_delete`, `instabox_snapshot_delete` | ask |
| `instabox_snapshot_restore` | always ask |
| Everything else | allow |

Channels without approval support (Telegram, Discord, scheduled runs) refuse
"ask" actions. Add an explicit rule, for example
`{"action": "instabox_machine_create", "resource": "*", "effect": "allow"}`, to
use them there. Rules match an action and a resource; they cannot tell the main
workspace from an auxiliary machine.

## Dashboard API

All routes live under `/dashboard-api/instabox` and require an admin session:
`machines`, `templates`, `operations`, `machines/{id}/ports`, `snapshots`,
`machines/{id}/restore`. The desktop uses `/dashboard-api/computers/instabox/{id}/*`. Deleting a machine or a
snapshot requires a JSON body `{"confirm": "<id>"}`.

## Live test

```bash
INSTABOX_LIVE=1 INSTABOX_API_KEY=ibk_... uv run pytest tests/test_instabox_live.py
```

The test creates a machine, runs a command, publishes a private port, starts
the desktop and takes a screenshot, snapshots, forks, restores and finally
deletes everything it created. Optional: `INSTABOX_API_URL`, `INSTABOX_TEMPLATE`.
