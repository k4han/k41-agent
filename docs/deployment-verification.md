# Deployment verification

This checklist targets one administrator and one runtime process on a personal
machine or internal server.

## Before installation or upgrade

- Run the Python suite and build/check the dashboard in CI.
- Use the release archive with its matching `.sha256` asset. Both installers and
  the updater reject missing, malformed, or mismatched checksums before extraction.
- Local clone installs and explicitly selected branch-source installs are
  development paths and do not consume release checksums.
- Sign in with `1234` on first use and set a private admin password when prompted.
  Default-password sessions can only change the password or log out. Existing
  private passwords continue to work; `k41 reset-password` remains available for
  local recovery.
- Keep `host: 127.0.0.1` unless remote access is required. For network access, use
  a firewall or VPN and HTTPS through a trusted reverse proxy. Keep CSRF enabled
  and list the actual frontend origins in `security.cors_origins`.
- For PostgreSQL, install matching `pg_dump` and `pg_restore` clients on PATH.
  A missing backup client aborts the update before source replacement.
- Windows entry points use the default Proactor event loop with SQLite.
  PostgreSQL retains SelectorEventLoop because Psycopg's async checkpointer
  does not support Proactor. That configuration still has a 512-socket limit;
  use SQLite or a non-Windows host when higher concurrency is required.

## Health and readiness

```sh
curl --fail http://127.0.0.1:4141/health
curl --fail http://127.0.0.1:4141/ready
```

`/health` confirms that the HTTP process responds. `/ready` returns HTTP 200
with `status: ready` and the installed version only after startup, checkpointer
initialization, and a successful database query. Unavailable persistence returns
HTTP 503. The probe does not call an AI provider or reveal connection errors.

Verify that the dashboard loads, a configured provider can answer a conversation,
workspace files can be accessed within the configured permissions, and enabled
channels work. Protected API requests require an authenticated session or a valid
Bearer token; unauthenticated requests to `/v1/api/*` return HTTP 401.

## Authentication checks

- First-use and legacy default-password logins open `/change-password`.
  Dashboard pages redirect there and protected APIs return HTTP 403 until the
  password is changed. A copied default-password Bearer token has the same limits.
- Weak replacement passwords are rejected. A valid change revokes the bootstrap
  session, and signing in with the new password grants normal access.
- Login cookies have `HttpOnly` and `SameSite=Lax`; HTTPS also sets `Secure`.
- The sixth login attempt from one peer within 60 seconds returns HTTP 429 and
  `Retry-After`. There is also a global limit of 30 attempts per minute. The peer
  address comes from the ASGI connection, rather than an arbitrary forwarding header.
- Password changes and `k41 reset-password` revoke all previously issued tokens.
- Logout uses CSRF-protected `POST /logout` and revokes all sessions for the
  single administrator, including copied Bearer tokens. `GET /logout` is not a
  state-changing endpoint.
- This upgrade requires existing users to sign in again because older JWTs do
  not include a persisted session version.

## Upgrade and recovery checks

The updater downloads and verifies the artifact before stopping the old process.
The installers refuse to replace source when server or tray shutdown cannot be
confirmed. After shutdown, the updater stores source plus a private
`.runtime-backup/` directory inside the retained `backup/app-<version>-<time>/`.

The persistence snapshot includes the database, `config.yaml`, runtime `.env`,
application `.env` files, and `data/runtime_config.key`. SQLite uses its online
backup API and an integrity check; PostgreSQL uses a custom-format dump.
`manifest.json` identifies the database and the original path of each saved file.
Snapshots contain sensitive material and should remain accessible only to the
installation owner. Keep independent backups of project storage, browser profiles,
and other workspace files; those directories are outside this upgrade snapshot.

Before reporting success, the updater runs migrations and verifies persistence
using the installed Python interpreter. A previously running web service must
also return readiness for the expected version. A previously stopped service
stays stopped. Channel-only services have no HTTP readiness endpoint; their
persistence is verified before the restarted process is checked.

Exercise these scenarios on an isolated copy of the runtime:

1. Reject an archive with a missing or altered checksum without changing source.
2. Refuse installation when the old process survives shutdown.
3. Fail a migration after changing a table and verify that the old source,
   conversation data, configuration, and matching encryption key are restored.
4. Fail startup/readiness and verify that the new process stops before restoring
   the database, followed by restarting the old service.
5. Refuse automatic restoration if the new process cannot be stopped. Preserve
   the backup and report its path for operator recovery.
6. Verify a successful update and confirm the expected version, saved
   conversations, decrypted provider credentials, and original running/stopped state.

If automatic recovery fails, keep the service stopped. Use the reported backup
path to restore the previous source and its locked dependencies. For SQLite,
restore `.runtime-backup/database.sqlite` to the database path recorded in the
manifest only after all database clients have stopped, and discard stale SQLite
WAL/SHM/journal files. Restore each saved configuration/key file to the original
path from the manifest; files recorded as absent must remain absent. For
PostgreSQL, restore `database.dump` with `pg_restore --dbname=<database> --clean --if-exists
--no-owner --no-privileges --exit-on-error --single-transaction` using the same
connection settings. Confirm readiness and credential decryption before enabling
access again.

## After deployment

Review startup/update logs and monitor readiness, disk space, failed logins, and
scheduled tasks. Restarted background tasks are marked interrupted and do not
automatically resume execution. Perform a longer reliability run and a restore
drill before relying on unattended operation with important data.
