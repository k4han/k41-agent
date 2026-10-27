# Durable project workspaces

A project owns its files independently of its execution provider. `Project.id`
is the durable identity. Existing catalog keys, labels, pinning, status and
conversation history survive replacement of a sandbox.

`ProjectWorkspace` owns a storage head and source reference. Local conversations
share a local home selected by `Project.local_workspace_id`. Cloud conversations
use conversation-owned `session` workspaces. GitHub issue/PR automation uses separate
`github_task` workspaces. `WorkspaceRuntime` contains the replaceable execution
reference and generation, with one current runtime per workspace.

The local home is presented as `main`. Existing `shared` workspaces and their
conversation bindings retain their IDs. A session leaving a shared environment
forks a verified snapshot into `session:<thread>`. Children and associated tasks
follow the owning conversation; other conversations retain their bindings.
Workspace resolution checks known paths, retired references and repository IDs
before creating directories or allocating sandboxes. New project conversations
use local execution. Moving an existing conversation is an explicit session switch.

## Storage contract

`workspace.project_storage_root` configures host storage; the default is
`~/.k41-agent/project-storage`, partitioned by database and workspace ID. Keep
this directory and the application database on persistent disks. The initial
deployment has one application instance performing writes.

`ProjectStorageBackend` uses logical IDs, immutable SHA-256 objects and versioned
manifests without exposing local paths. An S3 implementation can use the same
contract. Files are divided into chunks of at most 4 MiB; only missing chunks
are transferred. A compare-and-swap publishes a new database head after all
referenced objects have been verified. Partial uploads never replace a good head.

Capture reuses file hashes only while inode, device, size, mode, modification time,
change time and source path match the previous successful capture. Missing or
modified stored objects invalidate reuse. Disk storage caches checksum verification
against the same filesystem metadata; restore always verifies the bytes again.
Unchanged background checkpoints keep the existing save timestamp and avoid
republishing the database head or collecting storage.

The current revision, its predecessor, the linked local baseline and pinned migration revisions are kept.
Collection runs under the workspace lock and removes only unreferenced data.

Checkpoints include binary files, ignored/untracked work files, instructions,
project skills, deliverables and Git state. Dependency folders and known runtime
caches are excluded; `.gitignore` is not an exclusion policy. Within
`.k41-agent`, `scratchpad`, `uploads` and `generated-images` participate; tool
output logs retain their existing retention behavior.

Git worktrees become self-contained Git directories with their index and private
HEAD. Application installation tokens are removed from stored remote URLs.
External Git object alternates and unsupported filesystem entries fail
explicitly. Internal symlinks are stored without following them; links escaping
the workspace are rejected.

## Execution, failures and recovery

Mutations enter the workspace coordinator after authorization. File writes keep
optimistic version checks. Long processes release the lock when they yield and
are monitored independently of output reads. Local periodic checkpoints run every
30 seconds. Cloud monitoring backs off through 5, 15 and 60 seconds while work
or managed processes remain active. Idle cloud checks query provider state without
executing commands. Run/process completion and lifecycle operations checkpoint files.
Shared shell processes do not have transaction isolation.
Ready local workspaces remain monitored so external edits are saved even without an
attached conversation. This still scans filesystem metadata every 30 seconds;
unchanged files are not reread or rehashed. Unavailable and retired runtimes leave
the monitoring set. After a host restart, a stored `ready` status does not guarantee
that its cloud sandbox is still alive. When checkpointing detects an expired
sandbox, it records the runtime as unavailable, preserves the saved revision and
stops monitoring it. This does not provision a replacement in the background;
the next execution request restores the files into a new sandbox on demand.
Remote GitHub commit/push runs as one managed mutation.
Missing local checkpoint roots report their registered path. After restoring
the directory, a successful checkpoint retry resumes monitoring.

Execution receipts and persistence outcomes are separate. A failed save retries
storage, not completed commands. Disconnected calls retain `unknown_outcome`
and are never replayed automatically. Host receipts use durable workspace and
conversation identity; process control and permissions retain execution-specific
ownership.

A storage failure retains the runtime and blocks new managed writes. Reading,
stopping processes and retrying checkpoints remain available. A successful save
clears the block. A provider termination can lose changes after the last
successful checkpoint. These are file snapshots, not process-memory snapshots
or transactional backups of running databases.

Resuming an existing conversation resolves the durable project identity without
attaching or creating a sandbox. New conversations from the catalog, project
detail page, sidebar and project picker use local execution without moving or
checkpointing existing cloud sessions. Original local directories are adopted
as-is. Cloud-only projects materialize the latest verified saved revision into
a managed directory, without contacting the cloud provider. Missing saved data
requires saving or synchronizing first. Draft backend selection is deferred
until the first message, using `ChatRequest.execution_backend`. Resuming an existing
conversation retains the current runtime. Chat admission reserves the workspace
without initializing execution. Background task submission resolves its binding
and reserves the workspace when the task runs. Questions that do not use workspace tools can
complete without a cloud environment.

Cloud file browsing reads live files when the runtime is already running and may
read the saved revision when execution is unavailable, without starting execution.
Missing files, permission failures and invalid paths never fall back to old data.
`WorkspaceReadResult` reports source, generation, saved revision and fallback reason
for every data type. Raw text/binary reads require live data unless the caller
explicitly requests `saved_ok`; download/preview responses report source metadata
in `X-Workspace-*` headers. Project instructions and skill discovery also remain
available from saved files. Saved paths retain their cloud root;
temporary read views are never published as local execution workspaces. Active
skill instructions remain available from conversation state; their execution
cache and runtime tools are restored only when a coding tool needs them.

The first workspace tool, attachment write or explicit runtime operation attaches
the selected execution backend. A surviving sandbox is reused without restoring
an older checkpoint over live files. Stopped/archived Daytona runtimes resume
before capturing their files; finished Modal runtimes require replacement.
Network and authentication errors do not establish that a runtime was deleted.
A lost cloud sandbox is recreated on the
same enabled backend and hydrated from the saved revision before tools execute.
Restoration uses the workspace lock so concurrent conversations share one new
runtime and generation. Failed commands are never replayed by recovery.

A disabled backend or missing saved data produces an explicit tool error rather
than switching to local or cloning a fresh project. Legacy pending recovery
decisions no longer block ordinary messages and are resolved automatically when
execution is needed. Explicit same-backend restore and local recovery remain
available through `runtime.recovery`. Original local project directories remain
in place; manual local recovery applies a reviewed merge. Retired GitHub task worktrees can be materialized into
application-owned runtime directories.

Deleting a conversation removes its association and stops its own processes.
Pending approvals in that conversation and its descendants do not block deletion;
approvals in retained conversations still block sandbox retirement.
Managed cloud lifecycle actions save before stopping, archiving or deleting.
Shutdown stops managed processes and attempts final saves before closing the
database. GitHub worktree pruning preserves sources whose data is not saved.

## Migration and API

Additive schema migration preserves existing mappings and catalog metadata.
Each legacy source is captured separately. Different shared copies enter
`needs_selection`; the project page asks which copy should become primary.
Other copies stay pinned and are neither merged nor deleted. Unavailable
sources remain visible with an error. GitHub task workspaces remain separate.

Chat, task and workspace requests accept `project_id` and
`project_workspace_id`. Existing execution references remain supported.
Project responses add `migration_status`, `workspaces`, `runtime`, `sync_policy` and
`persistence`, including the last saved revision, timestamp and error.

- `POST /dashboard-api/projects/{id}/runtime/ensure`: resolve or recover files;
  accepts optional `project_workspace_id`. This explicit inspection endpoint records a pending recovery decision
  (code `provision_confirm_required`) instead of restarting a lost cloud
  runtime automatically.
- `POST /dashboard-api/projects/{id}/runtime/recovery`: apply the pending
  decision with `action` (`restore` on the previous backend, or `local`) and
  `expected_generation`; refuses disabled cloud backends for `restore`. Manual
  local recovery also requires the `preview_token` returned by `local-sync`.
- `POST /dashboard-api/projects/{id}/runtime/abandon`: clear an
  `allocation_unknown` transition after the operator reconciled provider
  inventory; accepts optional `project_workspace_id` and `expected_operation_id`.
  `POST /dashboard-api/workspace/runtime/abandon` does the same for a temporary
  thread workspace with its `thread_id` and `expected_generation`.
- `POST /dashboard-api/projects/{id}/checkpoint`: save or retry saving.
- `POST /dashboard-api/projects/{id}/migration/select`: select `source_id`.

Instruction and skill reads use saved cloud files independently of execution.
Workspace tools ensure execution on demand and report persistence separately. Archive/remove keep
data; these endpoints do not implement permanent file deletion.

## Switching execution backends

The dashboard conversation project picker switches only the selected session
between enabled local, Daytona and Modal backends. Each cloud session has its
own files and local baseline. Legacy sessions keep sharing their old cloud
workspace until moved individually. Project and conversation identities,
catalog labels, Git state, uploads and uncommitted files remain unchanged.
GitHub task workspaces remain independent.

`POST /dashboard-api/projects/{id}/runtime/switch` accepts `backend`,
`thread_id`, `expected_generation` and optional `project_workspace_id`. A missing
conversation ID is rejected. A `manual_apply`
cloud-to-local switch also requires `preview_token`. The endpoint returns `202`
with an `operation_id` and runs independently of the browser connection. The
thread runtime response exposes `runtime.transition`, including the phase,
target backend, saved revision, timestamps and errors. A same-backend request
returns the current binding without capturing or reloading files.
`GET /dashboard-api/workspace/runtime/{thread_id}` resolves a conversation's
current binding and transition without starting an execution environment.
It also returns `workspace_name`, `persistence`, `local_link` and the journal's
`transition_workspace_id` while a fork is pending. The conversation
picker shows the current physical location, workspace name and save state.
Save state refreshes while idle without replacing an unchanged workspace binding.

Switching requires an idle source workspace: active agent runs, background
tasks, managed processes and paused workflow requests block the operation with
`409`. All agent entrypoints reserve their workspace for their full run; a
switch reservation blocks new runs, managed writes and lifecycle operations.
Clients with obsolete generations also receive `409`. Dashboard session events
publish `workspace_runtime_changed`; the UI refreshes on these events, on
reconnection and focus, and polls during a switch.

The source is checkpointed and its revision pinned before provisioning. A fresh
destination is restored and verified before one database transaction publishes
the new runtime, increments its generation and refreshes the owning conversation,
children and associated task references. Journals retain source workspace and
binding generation until activation. For `manual_apply`, switching back to local requires a
preview token and uses the linked original directory, preserving independent host
edits through the same three-way application used by `local-sync`. The merged
local tree is captured in the local home's storage before that session is rebound.
Other local sessions keep their binding. Cloud-only projects
use a managed local directory, allocated only on explicit export or local demand.
Cloud-to-cloud switches depend on verified snapshots, not host mirror success.
Cloud sources are stopped after activation when no other workspace or thread
references the same sandbox. Modal retirement terminates the old sandbox.
Old application-owned `switch-*` local runtimes are removed after activation;
the original linked directory is retained. Legacy aliases resolve within their
workspace. Detached session runtime references report `stale_runtime` instead of
reattaching an old sandbox or registering a new project.

The latest transition and pending cleanup references are durable. Failure before
activation retains the source binding. A restart marks an unfinished transition
failed and cleans up its partially restored target; it never replays user
commands. Failed cleanup is retried by the workspace monitor. Provider allocation
IDs are saved before workspace initialization; an allocation whose provider
response is lost before its ID is received enters `allocation_unknown`. Operation
labels/tags allow inventory reconciliation after restart; an unresolved allocation
never triggers automatic duplicate provisioning. An operator who reconciled the
provider inventory can explicitly abandon the transition to unblock the
workspace without provisioning again. Recovery uses the same journal
and only publishes a verified destination. Shutdown waits for accepted switches
to settle before final saves.

File snapshots do not transfer processes, terminal state, installed dependencies,
system environment variables or guarantees of Windows/Linux compatibility. The
runtime stays warm between turns. External editors must pause writes during a
switch because application locks do not isolate external processes. This release
uses local durable storage and one application writer; S3 remains a future
implementation of the existing storage contract.

## Linked local directories

A project originally registered from a local directory retains that directory as
its local link. While it is the active runtime, successful checkpoints update
`local_sync_revision`. After moving execution elsewhere, this baseline remains
pinned independently of the current and previous workspace revisions.

New project and thread workspaces explicitly use `manual_apply`. Cloud checkpoints
save revisions without writing the original host directory. Applying changes or
continuing locally requires preview and explicit application. Existing cloud chat
workspaces migrate to `manual_apply` with their baselines intact. Existing local
and GitHub task policies remain unchanged. Migration is idempotent and preserves
the selected local home. The link is not an automatic
two-way filesystem sync. Source workspace files, snapshot persistence and local
application status are independent.

`POST /dashboard-api/projects/{id}/local-sync` accepts `expected_generation` and
optional `project_workspace_id`. Without `preview_token`, it checkpoints the
idle runtime and returns the target path, saved revision, planned changes,
conflicts and a preview token. Sending that token applies the reviewed changes.
Clients cannot supply a target path or bypass conflicts. A stale token or runtime
generation returns `409` and requires a new preview.
Manual GitHub task workspaces use the same preview endpoint with their explicit
`project_workspace_id`, including before local recovery of a lost sandbox.
Temporary thread workspaces expose the same contract at
`POST /dashboard-api/workspace/local-sync`, with `thread_id` and its existing
string generation. Both project and thread local switches accept `preview_token`.

The comparison is three-way: baseline, saved workspace and linked local files.
Independent local edits and ignored dependency/cache folders are preserved. If
both copies changed the same entry differently, all writes are blocked until
the conflict is resolved. Each sandbox retains its own last-applied baseline.
Source and local destination are reserved together in workspace ID order; runs,
processes and pending approvals on either side block synchronization. Successful
application updates the local storage head and source baseline without changing
the sandbox's generation or session binding. External editors should pause
writes while applying; files are rechecked before replacement, but the operation
does not provide a transaction across external processes.

Files are replaced atomically one at a time. On an I/O error or application
interruption, the saved workspace and baseline remain available; a new preview
recognizes already-applied entries and supports retrying the remainder. No user
command is replayed. Type changes prepare the complete replacement before moving
the original into a temporary backup. A durable recovery record restores that
backup if publication is interrupted, or removes it after successful publication.
Recovery runs before the next local preview. On Windows, comparisons retain the
file read-only flag and ignore unsupported POSIX directory and execute permissions.
Git worktree links are refused because their external Git metadata requires a
Git-aware merge. Legacy projects already running remotely
without a retained local baseline report `baseline_missing` and preserve the
original directory rather than guessing which local changes can be overwritten.

## Verification

```sh
uv run --locked pytest
pnpm dashboard:check
pnpm dashboard:build
pnpm --dir agent/delivery/http/dashboard/frontend test:projects-ui
pnpm --dir agent/delivery/http/dashboard/frontend test:nav-projects
pnpm --dir agent/delivery/http/dashboard/frontend test:workspace-ui
pnpm --dir agent/delivery/http/dashboard/frontend test:runtime-switch
```

Contract tests run against disk storage and a path-free in-memory object store.
Cloud transfer and recovery tests use provider doubles without cloud credentials.
