---
description: Diagnose missing commands, dashboard startup problems, model errors, and browser connection issues.
---

# Troubleshooting

## The terminal cannot find `k41`

Open a new terminal after installation so the updated PATH takes effect. On
macOS or Linux, make sure your shell profile has been reloaded. The installer
also configures fish through `~/.config/fish/conf.d/k41-agent.fish`.

If the command is still missing, run the [installer](./installation) again and
inspect its output.

## The dashboard does not open

Check the runtime first:

```sh
k41 status
```

Then stop and start it:

```sh
k41 stop
k41
```

The default address is [http://127.0.0.1:4141](http://127.0.0.1:4141).
If you changed the host or port, use the configured address. The public
documentation website and your local dashboard are separate sites.

## I cannot sign in

For a fresh installation, the login page shows the default admin password.
Reset your password from the machine running K41:

```sh
k41 reset-password
```

Restart or revisit the dashboard after completing the prompt.

## A model request fails

Open **Settings > Providers** and check:

- The provider is enabled and its credentials are valid.
- The model identifier matches a model available to your account.
- An OpenAI-compatible base URL points to the intended API endpoint.
- The provider account has the required quota or credit.

If you use a pool, inspect its member availability and
[recovery behavior](./model-pools#priority-and-recovery). A request rejected for
invalid input does not automatically switch to another model.

## The browser does not start

Install Chrome or Chromium on the K41 host and enable Browser tools for the
selected agent. If needed, configure `browser.executable_path` in Settings.

Run the task in a dashboard conversation. Channel and scheduler runs do not
provide the account identity required for browser sessions. See
[browser automation](./browser).

## A source installation has no dashboard assets

From the repository root:

```sh
pnpm install
pnpm dashboard:check
pnpm dashboard:build
```

Then start the runtime again. The dashboard build is generated and is not
tracked in Git.

## Find more detail

Inspect `~/.k41-agent/server.log`. On Linux, use
`journalctl --user -u k41-agent -e` for service logs.

When reporting an issue, include the K41 version, operating system, relevant
command or task, and the error message. Remove credentials and unrelated private
content from any shared logs.

[Open a GitHub issue](https://github.com/k4han/k41-agent/issues) with the details
needed to reproduce the problem.
