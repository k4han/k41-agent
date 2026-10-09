# Internal workspace storage

The project workspace holds source code, dependencies, project directories,
and deliverables. `.k41-agent` holds supporting internal files only. Agent
instructions keep shell commands rooted at the workspace or a project folder;
they do not impose an additional workspace/workdir restriction.

All storage uses real files, available to file tools and shell scripts:

| Path | Purpose | Lifetime |
| --- | --- | --- |
| `scratchpad/` | Project-shared drafts, notes, temporary helper scripts | Until explicitly removed or workspace deletion |
| `outputs/` | Project-shared retained tool text and process logs | Seven days |
| `uploads/` | User attachments | Existing workspace retention |
| `generated-images/` | Generated images rendered by the UI | Existing workspace retention |

Directories are created when writing, not during workspace initialization.
`assets/` and `memory/` are no longer created or suggested. Existing files in
those directories remain readable and are never migrated or deleted automatically.
Git exclusion supports normal repositories and worktrees.

All conversations and sub-agents using the same project workspace share these
directories. File paths do not contain conversation or thread keys. Agents
should read existing project notes and choose distinct filenames for independent
tasks. Retained output uses unique output IDs to avoid filename collisions.
The system prompt provides the shared scratchpad directory.

Notes survive turns, application restarts, and conversation reset/deletion,
including their host mirrors. Conversation cleanup stops that conversation's
processes without removing shared notes. Output expiry does not remove execution
journals or permission records, which remain scoped to their originating thread.
Sharing files does not share process control or permission approvals.
Deleting one conversation also preserves a temporary directory or sandbox that
is still bound to another conversation.

Existing files under legacy conversation-key directories remain readable by all
conversations in the workspace and are not moved or deleted automatically. Legacy
output references are copied lazily to the shared output directory when recovered.

Host hydration and synchronization cover `scratchpad/`, `uploads/`, and
`generated-images/` only, excluding symlinks. Output logs stay in their owning
workspace; arbitrary directories and legacy categories are not copied.

Long results return a preview plus `output_paths`. Read those files using
`read`; use `byte_offset=0` and `next_byte_offset` for exact long-line recovery.
Each result is bounded to 50 KiB/2,000 lines, with up to 10 MiB retained per
output. Capture or storage loss is explicit. A failed write returns no path.
Web download limits and provider-side limits still apply.

This behavior is shared by local, Daytona, and Modal runtimes. It does not use
a virtual filesystem or embed large files in LangGraph checkpoints.
