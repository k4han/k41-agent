# Skill packages

The agent supports the [Agent Skills format](https://agentskills.io/specification).
A package contains `SKILL.md` and any supporting files or directories. Python,
shell and JavaScript scripts use the existing `bash` process lifecycle; supporting
text and images use `read`. Binary templates and documents remain byte-exact and
can be processed by scripts supplied by the skill.

## Sources and precedence

Discovery order is the configured project directory (`.agent/skills` by default),
project `.agents/skills`, the managed user directory (`~/.k41-agent/skills`),
user `.agents/skills`, then `skills.additional_roots` in configuration order.
Project skills override shared skills. The dashboard identifies each package by
its source ID and shows shadowed, disabled and invalid packages. The existing
`allowed_skill_names` selection applies to shared skills; project skills remain
available unless disabled. Skill frontmatter cannot grant additional tool rights.

## Activation and resources

Type `/` in the chat input to search available skills and select one with the
mouse, Enter or Tab. Suggestions insert `/skill name` without sending the message.
Use `/skill name [task]`, `/skill load name [task]` or `skill(name)` to activate a
skill. Use `/skill unload name` or `skill(name, action="unload")` to release its
instructions. `/skill refresh name` or `skill(name, refresh=True)` loads the current
source version. Typing `/skill refresh ` or `/skill unload ` also suggests names.
Command keywords are case-insensitive; load, refresh and unload require a name.
Wait for `skill` loading to complete before reading or running its resources in
a subsequent tool batch. Coding tools authorize the entire batch against the
previously active skills, so a load and dependent resource access in the same
batch can stop that batch before execution.
Activation creates a complete snapshot in a shared content cache:

- Local: `~/.k41-agent/s/sk-<first-20-hash-characters>/` by default, alongside
  the managed skill repository. Set `skills.cache_root` to a dedicated absolute
  directory to choose a shorter location.
- Daytona and Modal: `.k41-agent/s/sk-<first-20-hash-characters>/` in the workspace.

The full content hash remains in the manifest and checkpoint. Installation is
atomic and checks that manifest before reuse. Conversations using the same
version share the prepared package; local workspaces also share its copy.
Existing checkpoints migrate to the short cache using their pinned archive.
Use the returned `skill_root` for resources and `workspace_root` for task inputs
and outputs. Loading a skill does not place every resource in the model context.

Active instructions and source versions are checkpointed separately from message
history, so manual and automatic compaction preserve them. Repeated activation
does not duplicate instructions. An existing conversation remains pinned to the
version it loaded until explicitly refreshed; permission changes are checked
again before instructions are restored. Snapshot archives remain pinned while
their conversation exists, including historical checkpoint versions. Deleted
conversations release those references; shared execution copies unused for seven
days expire separately. Deleting a conversation does not remove a shared copy
needed by another conversation. Running processes protect their active packages
with best-effort leases until they stop.

Local, Daytona and Modal use the same portable package worker. Cloud provisioning
can restore pinned snapshots without rereading a changed source package. Modal
replacement workspaces retain their logical skill workspace identity.

## Local source execution and shell syntax

In Skills settings, select **Local execution: Source**, or configure
`skills.local_execution_mode="source"`, to use local packages directly without
copying them. The default is `snapshot`, which pins a version and supports
recovery after source changes. Source mode checks the original package before
restoring instructions; a changed or removed source requires explicit refresh.
Packages containing `pyproject.toml`, `package.json` or `pnpm-workspace.yaml`
continue using snapshots so in-package dependency installation does not change
their source. Do not write task output or install environments in source mode.
Cloud execution always uses a prepared copy because it cannot access host files.

The `bash` tool runs the configured shell, which is normally PowerShell on
Windows. Activation instructions identify that shell and give its syntax. Each
execution receives `K41_WORKSPACE_ROOT` and a variable named after each active
skill, such as `K41_SKILL_LOGO_DESIGN`. A single active skill also receives the
alias `K41_SKILL_ROOT`. Keep the working directory at the workspace and use these
variables for scripts rather than copying absolute paths into commands:

```powershell
uv run --no-project --script (Join-Path $env:K41_SKILL_LOGO_DESIGN 'scripts/search_library.py') --subject sun --format paths --limit 40
```

```bash
uv run --no-project --script "$K41_SKILL_LOGO_DESIGN/scripts/search_library.py" --subject sun --format paths --limit 40
```

Use a script's own limit option when it supplies one. For PowerShell pipelines,
use `Select-Object -First 40`; POSIX `head` and `export` are unavailable by default.
Instructions and assets are read-only through file tools, and only active skills
grant resource access in the current agent and workspace. Existing read and shell
policies still apply. Shell execution retains the host's existing permissions.

## Dependencies

Python scripts should declare dependencies using PEP 723:

```python
# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx>=0.28,<1"]
# ///
```

Run them with `uv run --no-project --script <path>`. Use `--with` or
`--with-requirements` for explicitly documented dependencies. A bundled
`pyproject.toml` can instead use `uv run --project <skill_root>`. Respect bundled
lockfiles and reuse the uv cache. Do not infer package names from imports or
automatically retry scripts that may have performed side effects.

Use Node.js for plain JavaScript, `pnpm dlx` for package CLIs, and `pnpm --ignore-workspace install`
inside the snapshot when the package supplies `package.json`. Prefer
`--frozen-lockfile` with a bundled pnpm lockfile. Sandbox runtime setup provisions
missing uv, Node.js 22 and pnpm into `.k41-agent/skill-tools/bin`, using official
release artifacts and available checksums. Activation returns executable paths
and the PATH prefix needed by isolated shell calls. Local runtime installation
is left to the host; unavailable runtime, system package, network or credential
requirements must be reported instead of silently changing project dependencies.
The standalone pnpm option prevents discovery of a parent workspace and changes
to its dependencies. Omit it only when the skill supplies its own workspace file.
Windows resource paths account for child files when selecting the extended path
prefix, rather than only checking the package root. A short local cache avoids
inheriting deeply nested conversation workspace paths. Host programs that do not
support extended paths can still require a shorter cache or workspace location.

## Management API

The authenticated `/dashboard-api/skill-packages` API supports listing, creation,
source-aware enable/disable, rename and whole-package deletion. Pass a serialized
workspace reference in the `workspace` query parameter for project operations.

- `POST /validate` parses and validates a document on the backend.
- `POST /render` renders form edits while preserving unknown frontmatter fields.
- `GET /{id}` returns the raw document, parsed fields, diagnostics and file version.
- `PUT /{id}/document` merges form changes using `expected_version`.
- `GET /{id}/files` pages directory entries with `path`, `offset` and `limit`.
- `GET /{id}/files/{path}` previews text or returns metadata; `download=true`
  returns raw bytes.
- `PUT /{id}/files/{path}` uploads raw bytes; replacing an existing file requires
  its returned version in `If-Match`.
- `POST /{id}/files` creates directories, renames entries or deletes entries.
- `POST /imports/preview` inspects a host directory, ZIP upload or HTTPS Git source.
- `POST /imports` commits a preview, with explicit `overwrite` for conflicts.
- `GET /{id}/export` downloads a portable ZIP, preserving bytes and executable modes.

Git imports accept a ref and repository subdirectory. Private GitHub imports can
use an existing connected installation ID. The resolved commit is included in
the import receipt; imported packages update only when explicitly imported again.
Preview tokens expire after 30 minutes and are scoped to the application process.
Multi-package commits use a shared lock, staging and rollback on failure.

The legacy `/dashboard-api/skills` endpoints retain their original SKILL.md-only
CRUD semantics, including deletion. The new dashboard uses whole-package deletion.

Default limits are 10,000 files, 128 MiB per package and 64 MiB per file or ZIP
upload (`skills.max_files`, `skills.max_bytes`, `skills.max_file_bytes`). Activation
lists at most 200 resources; directory APIs page the remaining entries. Package
imports reject traversal, external links, special files, duplicate names and
names that cannot be represented consistently on Windows and Linux.

## Validation

Run Python checks using `uv run pytest`. In the dashboard frontend, run
`pnpm check`, `pnpm build` and `pnpm test`. The package tests exercise local storage,
both sandbox transports with simulated backends, binary round trips, concurrency,
snapshot recovery and the HTTP interfaces. Cloud integration tests require an
explicit configured environment; ordinary test runs do not create paid sandboxes.
Set `K41_TEST_DAYTONA_WORKSPACE` or `K41_TEST_MODAL_WORKSPACE` to an existing
`WorkspaceRef` JSON to enable `tests/test_skill_package_live.py`. These tests
prepare a unique snapshot, run uv, restore its original bytes after source changes,
and remove their own sandbox files. Run the dashboard Skills browser checks with
`pnpm test:skills-ui` after building the frontend.
