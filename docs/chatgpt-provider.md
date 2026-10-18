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
Complete sign-in in a browser on the computer running k41. Remote-browser,
container, and headless sign-in are not implemented by this provider.

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
