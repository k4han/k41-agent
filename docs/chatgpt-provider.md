# ChatGPT provider

k41 supports ChatGPT plan usage through the official Sign in with ChatGPT
public-client flow. An eligible ChatGPT Plus or Pro account can authorize
requests without an OpenAI API key.

## Connect

1. Run k41 and open **Settings > Providers**.
2. Select **ChatGPT**, name the configuration, and save it.
3. Select **Continue with ChatGPT**. Complete sign-in, select the workspace,
   and allow ChatGPT plan usage in the browser.
4. k41 discovers models available to that account and selects the first
   available model if the configuration has no available default model.
   Use **ChatGPT model** to change the selection.
5. Set the provider as default, or select it in an agent's model settings.

Each named configuration represents a separate account/workspace registration.
Create another ChatGPT configuration to use another account or workspace.
Reconnecting the same configuration reuses its issued client ID. **Sign out**
clears local tokens and attempts to revoke the renewable session, while keeping
the registration available for later sign-in.

Sign-in uses a short-lived listener at `http://127.0.0.1:<port>/auth/callback`.
When the browser runs on the same computer as k41, the callback completes
automatically. For a hosted server or container, k41 also lets you paste the
callback URL into the dashboard; no local k41 installation is required.

## Sign in to a hosted k41 server

The open-source ChatGPT flow requires a loopback redirect URI. Changing it to
the public k41 domain is not supported by dynamic registration. A browser on
your laptop therefore redirects to your laptop, not the remote k41 server.

### Connect from the dashboard without a local installation

1. Open the remote k41 dashboard over HTTPS and select **Continue with ChatGPT**.
   Keep the k41 tab open while signing in through the new tab.
2. When OpenAI redirects to `127.0.0.1` and the browser reports it cannot connect,
   copy the **entire URL from the address bar**, including `code`, `state`, and
   the issued `client_id` when present. An unavailable loopback page is expected
   in this flow.
3. Return to k41, paste the URL into **Callback URL**, and select
   **Complete sign-in**. k41 validates the original callback URI and state,
   exchanges the code using the saved PKCE verifier, and verifies the ID token.
4. k41 discovers models and stores the renewable connection on the server.

The pasted URL is sent in an authenticated, CSRF-protected POST body. It is
never fetched by the server or stored in browser storage. Only the browser's
original loopback URL contains the short-lived authorization code; k41 never
adds it to a dashboard URL or returns tokens to the interface.

Use a fresh sign-in attempt if it expired or k41 restarted. **Cancel sign-in**
ends a pending attempt without signing out an existing account. After a page
reload, selecting **Continue with ChatGPT** resumes a still-pending attempt.
The server keeps its own persistent host ID and owns subsequent token refreshes.

This callback relay is a k41 implementation, not the local-session transfer
procedure documented by OpenAI. It retains the same loopback redirect URI and
server-held PKCE verifier for the authorization and token exchange. Automated
tests cover the relay using mocked OpenAI responses; a real sign-in is still
needed to verify it against OpenAI in a particular installation.

### Transfer a local session (OpenAI VM procedure)

Use this alternative if you already have a local k41 connection and want to
move it to the server following OpenAI's documented VM procedure.

1. Run k41 on your local computer. Create a ChatGPT provider, for example
   `personal`, and complete **Continue with ChatGPT** there. Select the account
   and workspace that the server will use.
2. Stop the local k41 runtime so pending sign-ins and requests finish before
   the handoff. Export only that registration:

   ```sh
   uv run k41 chatgpt export-session --provider personal --output chatgpt-session.k41
   ```

   Enter and confirm a strong passphrase of at least 12 characters when prompted.
   The file is encrypted and authenticated with a passphrase-derived key.
   After saving it successfully, the command removes local tokens **without
   revoking the session**, allowing the server to take over refreshes.
3. Copy the encrypted file to the server over SSH:

   ```sh
   scp chatgpt-session.k41 user@server:~/chatgpt-session.k41
   ```

4. In the server dashboard, create a fresh ChatGPT provider, for example
   `personal`, without starting browser sign-in. Run the import on the server
   as the same operating-system user that runs k41:

   ```sh
   uv run k41 chatgpt import-session --provider personal --input ~/chatgpt-session.k41
   ```

   Enter the same passphrase. The destination provider name may differ from
   the local name. Import creates a server host ID if needed and preserves
   any existing server host ID and local encryption key. It re-encrypts only
   the selected registration into `~/.k41-agent/data/chatgpt/credentials.db`.
   Existing connected providers and registrations for another account or
   workspace are not overwritten; use a fresh provider for those cases.
5. Reload the server's provider settings, select **Refresh ChatGPT models**,
   choose a default model, and verify a completed request. Delete the transfer
   file from both computers after verification. Keep it until import succeeds
   because the source no longer holds the renewable session.

For containers, run import inside the container using the runtime's user and
persisted configuration volume. Both commands accept
`--directory /path/to/data/chatgpt` when the runtime uses a different credential
directory. Do not replace the server's entire credentials database or copy the
laptop's encryption key or host ID. No public OAuth callback port is required.

The transferred file includes the issued `client_id`, access and refresh
tokens, retained ID token, granted scopes, expiry, and account metadata.
Passphrases are entered through hidden prompts and are not command arguments.
Let the server alone refresh this transferred session; do not import the same
file into several active hosts. OpenAI notes that host-specific attribution and
revocation of plan access for transferred sessions are not yet available.

## Requests and credentials

- The provider always uses `https://api.openai.com/v1/responses`, with
  `store=false` and `stream=true`, including non-streaming application calls.
- A request succeeds only after `response.completed`. Failed, incomplete,
  and interrupted streams raise an error.
- System messages are sent as developer messages while preserving their content
  and position in the history. Local function/custom tools are supplied through
  developer-role `additional_tools` input items, preserving their original names.
- Unsupported sampling, output-limit, and state parameters are omitted according
  to the ChatGPT plan preview contract. These restrictions are specific to this
  provider and do not change API-key providers.
- Access tokens renew before expiry. SQLite transactions serialize rotating
  refresh-token updates across threads and processes on the same runtime.
- Tokens are encrypted at rest under the k41 configuration directory's
  `data/chatgpt/credentials.db`. The local encryption key is in
  `data/chatgpt/credentials.key`; protect this directory and its backups.
- Tokens are not included in dashboard settings or stored in browser storage.
  PKCE, state, and nonce are fresh for every sign-in. ID-token signature,
  issuer, audience, expiry, and nonce are verified before saving credentials.
- **Manage usage** opens ChatGPT Settings, where users can review usage,
  adjust available limits, or disconnect k41. Using the ChatGPT plan does
  not grant k41 access to ChatGPT conversation history.

This integration does not automatically switch to paid OpenAI API billing when
the plan reaches its limit. OpenAI availability and usage limits still apply.
The documented open-source/local flow is distinct from approval for paid or
remotely hosted offerings.

## Validation

Run `uv run pytest tests/test_chatgpt_provider.py` for local OAuth, refresh,
storage, and Responses stream contract tests. These tests use generated
signatures and mocked HTTP responses; they do not use a ChatGPT subscription.
A real sign-in and completed inference request are needed to verify account
eligibility and plan access in a particular installation.

References:

- [Official integration cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
