# Project secrets

Open a project and select **Secrets** to add credentials such as `DEPLOY_TOKEN`
or `DATABASE_URL`. Each project owns one shared set of secrets. Names must be
uppercase environment variable names; runtime system variables and `K41_` names
are reserved. Values contain up to 16 KiB of UTF-8 text without null characters.

Enabled secrets are supplied automatically to new project commands on Local,
Daytona, and Modal, including background tasks and child tasks. Disabling,
replacing, or deleting a secret affects subsequently started processes. Restart
an existing application process to give it updated credentials. A collision with
an active skill environment variable prevents the command from starting.

Values are encrypted in the database using the existing server-owned
`data/runtime_config.key`. Back up the database and its matching key securely.
The key stays on the server. Missing or invalid keys prevent execution with
enabled secrets; management listings remain available. Archiving or removing a
project from the catalog preserves its secrets for restoration.

The dashboard API returns names, enabled states, and timestamps. Stored values
cannot be retrieved from the management API. Editing without a new value keeps
the existing credential. Use Delete to remove it. Secret management works while
the execution workspace is stopped.

Only enabled variable names enter the agent's project context. Execution values
are injected into individual subprocess environments, without changing the
server's environment or creating a project `.env` file. Sandbox RPC uses private,
temporary files outside the project workspace and removes them before dispatch.

Process stdout and stderr are redacted before retention, including matches split
across pipe reads. This protects against accidentally printing credentials.
Code running with a secret can read it and deliberately transform or transmit it;
redaction is not an isolation boundary against untrusted project code.
