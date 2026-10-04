# Internal workspace storage

The project workspace holds source code, dependencies, project directories,
and deliverables. `.k41-agent` holds supporting internal files only. Agent
instructions keep shell commands rooted at the workspace or a project folder;
they do not impose an additional workspace/workdir restriction.

All storage uses real files, available to file tools and shell scripts:

| Path | Purpose | Lifetime |
| --- | --- | --- |
| `scratchpad/<conversation-key>/` | Drafts, notes, temporary helper scripts | Until conversation reset/deletion |
| `outputs/<thread-key>/` | Retained tool text and process logs | Seven days |
| `uploads/` | User attachments | Existing workspace retention |
| `generated-images/` | Generated images rendered by the UI | Existing workspace retention |

Directories are created when writing, not during workspace initialization.
`assets/` and `memory/` are no longer created or suggested. Existing files in
those directories remain readable and are never migrated or deleted automatically.
Git exclusion supports normal repositories and worktrees.

Conversation keys are the first 24 hexadecimal characters of SHA-256 of the
root thread ID. A root thread is the portion before `:sub:`. Sub-agents share
their conversation's scratchpad and should choose distinct filenames. Output
keys hash the complete thread ID, preserving output ownership boundaries.
The system prompt provides the exact scratchpad directory.

Notes survive turns and application restarts. Resetting or deleting a
conversation clears its scoped scratchpad and host mirrors, including notes
written by sub-agents. Closing a chat window does not reset a conversation.
Output expiry does not remove execution journals or permission records.

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
