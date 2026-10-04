# Workspace coding tools

Local, Daytona and Modal workspaces share one coding tool catalog and the same
input/result contracts. There is no agent tool profile selector. The catalog,
capabilities, agent allow lists, MCP tools, images, scheduling and agent control
tools retain their existing interfaces.

## Runtime boundary

`agent/modules/tools/coding` owns Pydantic input/result contracts, invocation
context, operation permissions, durable settlement, process management,
output retention and file operations. `ToolDefinition` supplies execution,
an output schema and optional rendering. The LangChain adapter returns a
`ToolMessage` with structured `artifact` metadata and `status="error"` for
errors and partial failures. `ToolResult.content` is model-facing;
`display_content` is stored only in the message artifact and used by live UI
events and reloaded conversation history. Cancellation and LangGraph interrupts
propagate; media content is preserved. Existing control tools retain their
`Command` semantics through their existing adapters.

Direct tool invocations without a runtime tool call ID return model content
only, never a serialized result containing UI fields. Before model invocation
or conversation summarization, coding artifacts are removed from a copy of
each tool message. Older journal results and checkpoint messages containing
mutation diffs are rendered as receipts from their metadata without replaying
file changes; the original UI history remains available.

Context includes agent, canonical workspace, thread, message and tool call ID.
The workflow prepares all coding calls in a model turn before running them.
Permissions are checked again at actual file access. Per-file locks serialize
mutations; invocation locks serialize duplicate calls in one container.

## Configuration

In the dashboard agent Tools tab, select these tools:

```yaml
tools:
  - read_file
  - list_dir
  - glob
  - grep
  - edit_file
  - write_file
  - apply_patch
  - exec_command
  - read_process_output
  - write_process_input
  - stop_process
  - read_tool_output
```

Old `run_bash` and `bash` allow-list entries normalize to `exec_command` on
every backend. Process-control entries normalize to the corresponding process
tools; `bash_list_sessions` is retired. Names are deduplicated before policy
filtering. Persistent session arguments and cwd/environment state do not carry
over. Existing agent files can still be parsed; saving writes canonical tool
names and omits the retired profile field. Legacy tools are not registered in
the public catalog.

## Sandbox runtime

Daytona and Modal lazily receive a checksum-verified zip of the shared file,
process, path and storage engine. Minimal package initializers keep application
bootstrap, LangChain and Pydantic out of the sandbox. A Linux image must provide
`python3` version 3.11 or newer; missing or incompatible Python produces an
explicit initialization error. No package installation or network download is
performed inside the sandbox. Local shell configuration applies to the host;
sandboxes select their own Bash/sh executable.

A daemon owns processes, stdin, byte cursors and file locks across calls.
Provider filesystem APIs transfer requests/results, avoiding command-line size
limits and stdout truncation for images and diffs. An authenticated Unix socket
inside a private runtime directory carries RPC. The application validates tool
inputs, settles invocations durably and approves canonical sandbox resources.
The worker checks the exact approved action/resource/metadata again at actual
access, rejecting resources that changed after preparation. Workspace ownership
includes backend, sandbox locator, root and thread so identically named paths
in different sandboxes cannot share results or approvals.

Output and invocation records persist outside the workspace under
`~/.k41-agent/coding-runtime/<application-namespace>`. The worker also records
settlements before acknowledging mutations. Lost acknowledgements never cause
automatic retries or sandbox recreation. Processes stop when their conversation
is cancelled or the application closes. A worker exits after 15 idle minutes;
process timeouts remain enforced even if the application disconnects. Process
IDs are not recovered after application restart. Provider sandbox termination
also terminates its daemon and children.

## Permissions and approvals

Global settings `tools.permissions`, `tools.shell`, and `tools.storage_root`
are database-owned runtime settings. Settings > Tools exposes a validated JSON
rule editor and the local shell/storage settings. Shell/storage changes require
an application restart. Operator YAML can seed these settings
at first startup. Agent `tool_permissions: null` inherits global rules;
an explicit list, including `[]`, replaces them. Last matching rule wins.
Rules use action/resource glob matching. File resources are canonical absolute
paths, with slash and Windows case normalization. Shell resources are the
entire command; approval identity also includes the shell and cwd.

```yaml
tool_permissions:
  - {action: edit, resource: "*/secrets/*", effect: deny}
  - {action: shell, resource: "*", effect: ask}
```

Actions include `read`, `edit`, `glob`, `grep`, `shell`, `external_directory`,
`read_tool_output`, `read_process_output`, `write_process_input`, and
`stop_process`. Default workspace reads/searches/writes and shell calls are
allowed. Explicit external paths require approval. Relative traversal and
symlink escapes are rejected. Mandatory destructive-command guards apply
before configurable authorization. Shell commands execute with host-user OS
authority; command prefixes do not imply filesystem confinement.

Dashboard approvals offer Allow once, Allow in this conversation, or Deny.
Conversation grants authorize the exact agent/action/resource/metadata/rule
identity within that workspace/thread; changing policy invalidates the grant.
Unsupported channels return `approval_unavailable`. Reloading the dashboard
restores pending requests from the checkpoint. Resume IDs are correlated with
the actual request; unrelated resume payloads cannot authorize an operation.

Invocation journals key on agent/message/tool_call_id within a workspace/thread
and record the tool/argument fingerprint. Completed results are reused before
authorization during replay. Reusing an ID with changed arguments is rejected.
An incomplete journal after interruption/restart returns `unknown_outcome`;
inspect side effects manually before issuing a new call. No side effect is
automatically retried. Journals are retained separately from expiring output
artifacts so output expiration cannot re-enable an old mutation.

## Processes and output

`exec_command(command, workdir, timeout_seconds=120, yield_time_ms=1000)` starts
a fresh process in the selected workspace. Timeout is 1–600 seconds; initial yield is 0–30,000 ms.
Windows uses PowerShell by default; Linux uses Bash/sh. Long commands use
temporary scripts, cleaned after completion. A running call returns its
`process_id`. `read_process_output` takes an explicit byte cursor and returns
the next cursor. Cursor reads are repeatable and advance only through content
actually returned. UTF-8 boundaries and line/byte limits are enforced.
`write_process_input` sends text exactly, with no implicit newline.
`stop_process` terminates the owned process tree.

Output is drained in worker I/O threads and observed asynchronously, supporting
Windows event loops without asynchronous pipe support. Captured blocks retain
stdout/stderr labels and byte lengths. The head/tail ring is at most 1 MiB;
retained output is at most 10 MiB per job. Beyond that quota, pipes continue
draining and `capture_truncated` reports permanent loss. A disk write failure
also disables further storage while continuing to drain pipes.

Model text is at most 50 KiB/2,000 lines including truncation notices. Initial
large shell output includes a labeled tail preview; the explicit cursor still
continues through the retained middle. Subsequent cursor reads contain only
their contiguous page. `output_truncated` describes omitted model content,
independently of capture loss. Ordinary nonzero exits are reported in
`exit_code`; timeout/cancellation produce structured errors.

Artifacts default to `~/.k41-agent/coding-v2/<database-hash>/<owner-hash>`,
outside repositories. An optional storage root still includes a database
namespace. Opaque `output_ref` values can only be read in their owning
workspace/thread through `read_tool_output(offset=1, limit=2000)`. Output files
expire after seven days, with cleanup at startup and periodically when new
outputs are created. Search excludes the output store even when a custom root
is placed inside a workspace.

Jobs survive chat turns. Stopping/deleting a conversation, deleting its
temporary workspace, or closing the application stops jobs and releases
process resources. Windows starts shells suspended before assigning Job Objects,
tracks descendant identities for launchers that use independent jobs, and
cancels blocked synchronous pipe reads before cleanup. Linux process
groups are killed, with bounded pipe-drain waits. There is no PTY or process
recovery after application restart.

## File operations

`read_file` streams numbered pages and returns `next_offset` plus a SHA-256
content version. Pages reserve space for metadata inside 50 KiB/2,000 lines.
Very long physical lines receive an explicit truncated preview. Version hashing
is streamed but requires a full-file pass. Supported image signatures are
checked before ingestion and images over 5 MiB are rejected.

`grep` validates regexes and supports `fixed_strings=True`. Ripgrep enumerates
gitignore-aware candidates and searches validated file batches. Python is the
fallback when ripgrep is absent; its existing ignored directories are retained.
Each resolved candidate receives a file permission check, including symlinks.
Results identify engine, limit and result truncation.

`edit_file` requires exact text; missing/ambiguous/no-op edits are rejected.
`expected_version` is optional on edit/write. Writes recheck current content
immediately before replacement, preserve UTF-8 BOM, CRLF/LF and file mode,
and atomically replace prepared files. Exclusive hard-link creation rejects
files created concurrently. Filesystems without hard-link support fail safely.
Text mutation inputs are capped at 10 MiB per file. External writers are not
locked by the application; the final check/replacement gap is not a filesystem
transaction against arbitrary outside processes.

`apply_patch` accepts `*** Begin Patch` with Add File, Update File contextual
`@@` hunks, Delete File, and `*** End Patch`. Moves, numbered unified-diff
hunks and duplicate resolved paths are rejected. All targets are authorized,
prepared, locked in stable order, and version-checked before the first write.
Writes are sequential. A later write error returns `partial_failure` with
applied/pending files; retrying the whole patch is unsafe. Mutations return
added/deleted line counts and new versions to the model, and invalidate skill
caches. Diffs are retained in UI artifacts instead of echoing submitted file
content into the model context. UI text is bounded independently;
`display_truncated` describes omitted UI content, while `output_truncated`
continues to describe omitted model content. Large diffs are paged through
output references.

## Verification and measurement

Run `uv run python -m pytest -q`, `pnpm dashboard:check`, and
`pnpm dashboard:build`. `.github/workflows/coding-tools.yml` runs the coding
and compatibility contracts on Windows/Linux, plus dashboard validation.
Tests cover real pipes, Unicode, stdin, nonzero exits, timeout, descendant
cleanup, cursor boundaries, ownership, approvals/replay, large pages, images,
BOM/CRLF, permissions, concurrent creation/edits, prepared/partial patches, and the shared sandbox engine and transport.
Symlink tests skip when Windows symlink privileges are unavailable.

Run `uv run python scripts/benchmark_coding_v2.py --baseline-ref <old-commit>
--output docs/coding-v2-benchmark.json` from the repository root. Each case runs
in a fresh process and measures elapsed time, Python allocation peak, sampled
RSS delta, and model content bytes. The baseline shell kernel is loaded from
the selected git revision without registering tools. Read measurements compare
the original full-read-then-page behavior with versioned streaming paging.
The checked-in report records the measured platform and commit. A single
sample is a memory regression check, not a statistically valid speed claim.
Execution logs include duration/status/byte counts/truncation and hashed
permission resource identities, without command or output contents.

The recorded Windows run compares 1/8/32 MiB outputs. The new capture ring
stays at 1 MiB; shell Python allocation peak stays around 2.3 MB while the
32 MiB legacy case reaches 69.2 MB. The 32 MiB versioned read uses about
150 KB of Python allocations versus 73.5 MB for the full-read baseline.
The shell sample takes 0.76 s versus 0.42 s in the baseline: bounded retention
and descendant tracking add overhead. Reliability and bounded memory are the
acceptance targets; this change does not promise faster shell execution.
