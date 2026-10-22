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
Deleting the final conversation also preserves its catalog entry. Existing
sandbox expiration and conversation cleanup policies are unchanged; unavailable
workspaces remain in the catalog and report an error when opened.

## Dashboard API

All endpoints use the dashboard's existing authentication and CSRF protection.

- `GET /dashboard-api/projects`: accepts `status` (`active`, `archived`, `removed`,
  or `all`), `search`, `limit` (1–100), and `after`.
- `POST /dashboard-api/projects`: accepts `workspace` (`WorkspaceRef` or
  `WorkspaceBinding`) and an optional `label`.
- `PATCH /dashboard-api/projects/{id}`: accepts `label`, `is_pinned`, and/or
  `status` (`active` or `archived`).
- `DELETE /dashboard-api/projects/{id}`: marks the project as removed.

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
uv run pytest tests/test_projects.py tests/test_nav_projects.py
pnpm dashboard:check
pnpm dashboard:build
pnpm --dir agent/delivery/http/dashboard/frontend test:nav-projects
pnpm --dir agent/delivery/http/dashboard/frontend test:projects-ui
pnpm --dir agent/delivery/http/dashboard/frontend test:navbar-ui
```

Browser verification uses an installed Chromium browser, or the executable set
in `BROWSER_EXECUTABLE`, with isolated temporary profiles and mocked API data.
