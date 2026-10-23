# Project catalog

Open `/projects` or select **Manage** beside **Projects** in the sidebar to add,
search, rename, pin, archive, remove, or restore projects. A project can exist
without any conversations. **View history** opens the existing workspace filter;
**New chat** checks workspace availability before opening a fresh conversation.

The catalog uses the existing `WorkspaceScope.key` identity. GitHub execution
workspaces for the same repository remain one project. Renaming changes only
the display name. Temporary workspaces and conversations without a workspace
keep their existing navigation groups and are not catalog entries.

Normal root conversations and background tasks register their workspace
automatically. Startup imports missing projects from live conversation mappings
and saved background tasks, preferring conversation workspace mappings and the
most recent conversation for a shared scope. Repeated imports preserve catalog
metadata. Explicitly adding the same workspace reuses its project and restores
it to active status.

Archived and removed projects are hidden from the sidebar. Removal only changes
catalog status: it does not delete conversation history, files, or sandboxes.
Continuing a conversation or restarting the application never restores these
projects automatically. Use the corresponding status filter to restore them.
Deleting the final conversation preserves its catalog entry and durable files.
Project workspaces own their saved files independently of cloud sandbox
expiration. Opening a project resolves its current runtime and restores saved
files when necessary. See [durable project workspaces](project-workspaces.md).

## Project resources

Select a project name or **View project** in its actions menu to open
`/projects/{id}`. The **AGENTS.md** tab previews the root instruction document as
Markdown or source, and supports manual editing and creation. Creating a document
does not write a file until it is saved. Unsaved edits prompt before navigation,
and version conflicts preserve the draft until it is discarded or reloaded.
Only the root `AGENTS.md` is managed; nested instructions and `CLAUDE.md` are not
discovered. This view does not change instruction loading during agent execution.

The **Skills** tab lists project and shared packages together, retaining source
precedence, disabled states, shadowing and diagnostics. Packages and their paged
resource directories are read-only here. Documents can be previewed as Markdown
or source, and resources can be read or downloaded. Use Skills settings for
package management. The selected tab survives reloads through
`?tab=instructions` or `?tab=skills`.

Resource access applies to all catalog statuses and never changes catalog status.
Instruction and skill reads can use the last saved copy when execution is
unavailable. Writes ensure the project's runtime and report storage failure
separately from the completed file mutation.

## Dashboard API

All endpoints use the dashboard's existing authentication and CSRF protection.

- `GET /dashboard-api/projects`: accepts `status` (`active`, `archived`, `removed`,
  or `all`), `search`, `limit` (1–100), and `after`.
- `POST /dashboard-api/projects`: accepts `workspace` (`WorkspaceRef` or
  `WorkspaceBinding`) and an optional `label`.
- `PATCH /dashboard-api/projects/{id}`: accepts `label`, `is_pinned`, and/or
  `status` (`active` or `archived`).
- `DELETE /dashboard-api/projects/{id}`: marks the project as removed.
- `GET /dashboard-api/projects/{id}`: returns the existing project summary.
- `GET /dashboard-api/projects/{id}/instructions`: returns `path`, `exists`,
  `content` and `version` for the root `AGENTS.md`. A missing document returns
  empty content, `exists=false` and `version=null`.
- `PUT /dashboard-api/projects/{id}/instructions`: accepts `content` and a
  required `expected_version` (SHA-256, or `null` for creation). Stale versions,
  deleted documents and creation conflicts return HTTP 409. Workspace identity
  is resolved from the project record; clients cannot select another path.

Instruction documents must be valid UTF-8 text without null bytes and at most
300,000 bytes, matching the workspace text limit. Writes use the portable worker's
version check and atomic replacement on local, Daytona and Modal backends.

List responses contain `projects`, `has_more`, and `next_cursor`. Pinned projects
sort first, then by the immutable scope key using binary ordering. Cursors encode
both pin status and key. Navigation retains at most four conversation previews
per project, including the selected conversation, and supports independent
conversation pagination without loading checkpoints.

The `projects` table is created through the existing ORM initialization, before
the startup import. SQLite and PostgreSQL use transactional conflict handling to
avoid duplicates when conversations register the same project concurrently.

## Verification

```sh
uv run pytest tests/test_projects.py tests/test_project_resources.py tests/test_nav_projects.py
pnpm dashboard:check
pnpm dashboard:build
pnpm --dir agent/delivery/http/dashboard/frontend test:nav-projects
pnpm --dir agent/delivery/http/dashboard/frontend test:projects-ui
pnpm --dir agent/delivery/http/dashboard/frontend test:navbar-ui
```

Browser verification uses an installed Chromium browser, or the executable set
in `BROWSER_EXECUTABLE`, with isolated temporary profiles and mocked API data.
