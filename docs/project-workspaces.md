# Durable project workspaces

A project owns its files independently of its execution provider. `Project.id`
is the durable identity. Existing catalog keys, labels, pinning, status and
conversation history survive replacement of a sandbox.

`ProjectWorkspace` owns a storage head and source reference. Normal conversations
share its `shared` workspace. GitHub issue/PR automation uses separate
`github_task` workspaces. `WorkspaceRuntime` contains the replaceable execution
reference and generation, with one current runtime per workspace.

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

The current revision, its predecessor and pinned migration revisions are kept.
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
are monitored independently of output reads. Periodic checkpoints run every
30 seconds. Shared shell processes do not have transaction isolation.
Ready workspaces remain monitored so external edits are saved even without an
attached conversation. This still scans filesystem metadata every 30 seconds;
unchanged files are not reread or rehashed. Unavailable and retired runtimes leave
the monitoring set. Remote GitHub commit/push runs as one managed mutation.

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

Recovery first tries the current runtime and never overwrites live files with
an older checkpoint. A lost cloud runtime is replaced and hydrated before its
new generation becomes ready. Missing runtime and missing saved data produce
an explicit error instead of a fresh clone. Local project directories are not
moved or overwritten. Retired GitHub task worktrees can be materialized into
application-owned runtime directories.

Deleting a conversation removes its association and stops its own processes.
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
Project responses add `migration_status`, `workspaces`, `runtime` and
`persistence`, including the last saved revision, timestamp and error.

- `POST /dashboard-api/projects/{id}/runtime/ensure`: resolve or recover files;
  accepts optional `project_workspace_id`.
- `POST /dashboard-api/projects/{id}/checkpoint`: save or retry saving.
- `POST /dashboard-api/projects/{id}/migration/select`: select `source_id`.

Instruction and skill reads can use saved files while execution is unavailable.
Writes ensure execution and report persistence separately. Archive/remove keep
data; these endpoints do not implement permanent file deletion.

## Verification

```sh
uv run --locked pytest
pnpm dashboard:check
pnpm dashboard:build
pnpm --dir agent/delivery/http/dashboard/frontend test:projects-ui
pnpm --dir agent/delivery/http/dashboard/frontend test:nav-projects
pnpm --dir agent/delivery/http/dashboard/frontend test:workspace-ui
```

Contract tests run against disk storage and a path-free in-memory object store.
Cloud transfer and recovery tests use provider doubles without cloud credentials.
